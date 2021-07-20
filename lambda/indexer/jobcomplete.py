import os
import json
import textwrap
import urllib

from common import logger
from common import INDEX_ID, DS_ID
from common import S3, TRANSCRIBE, KENDRA
from common import stop_kendra_sync_job_when_all_done
from common import get_file_status, put_file_status

def parse_s3url(s3url):
    r = urllib.parse.urlparse(s3url, allow_fragments=False)
    bucket = r.netloc
    key = r.path
    file_name = key.split("/")[-1]
    return [bucket, key, file_name]

def put_document(dsId, indexId, s3url, text):
    logger.info(f"put_document(dsId={dsId}, indexId={indexId}, s3url={s3url}, text='{text[0:100]}...')")
    bucket, key, file_name = parse_s3url(s3url)
    # get bucket location.. buckets in us-east-1 return None, otherwise region is identified in LocationConstraint
    try:
        region = S3.get_bucket_location(Bucket=bucket)["LocationConstraint"] or 'us-east-1' 
    except Exception as e:
        logger.info(f"Unable to retrieve bucket region (bucket owned by another account?).. defaulting to us-east-1. Bucket: {bucket}")
        logger.info(e)
        region = 'us-east-1'
    file_status = get_file_status(s3url)
    if file_status:
        documents = [ 
            {
                "Id": s3url,
                "Title": file_name,
                "Attributes": [
                    {
                        "Key": "_data_source_id",
                        "Value": {
                            "StringValue": dsId
                        }
                    },
                    {
                        "Key": "_data_source_sync_job_execution_id",
                        "Value": {
                            "StringValue": file_status['execution_id']
                        }
                    },
                    {
                        "Key": "_source_uri",
                        "Value": {
                            "StringValue": "https://s3." + region + ".amazonaws.com/" + bucket + key
                        }
                    }
                ],
                "Blob": text
            }
        ]
        logger.info("KENDRA.batch_put_document: " + json.dumps(documents)[0:1000] + "...")
        result = KENDRA.batch_put_document(
            IndexId = indexId,
            Documents = documents
        )
        if 'FailedDocuments' in result and len(result['FailedDocuments']) > 0:
            logger.error("Failed to index document: " + result['FailedDocuments'][0]['ErrorMessage'])
        logger.info("result: " + json.dumps(result))
        put_file_status(s3url, file_status['lastModified'], file_status['status'], "NONE", "DONE")
    return True

def prepare_transcript(transcript_uri):
    logger.info(f"prepare_transcript(transcript_uri={transcript_uri[0:100]}...)")
    response = urllib.request.urlopen(transcript_uri)
    transcript = json.loads(response.read())
    items = transcript["results"]["items"]
    txt = ""
    sentence = ""
    for i in items:
        if (i["type"] == 'punctuation'):
            sentence = sentence + i["alternatives"][0]["content"]
            if (i["alternatives"][0]["content"] == '.'):
                #sentence completed
                txt = txt + " " + sentence + " "
                sentence = ""
        else: 
            if (sentence == ''):
                sentence = "[" + i["start_time"] + "]"
            sentence = sentence + " " + i["alternatives"][0]["content"]
    if (sentence != ""):
        txt = txt + " " + sentence + " "
    out = textwrap.fill(txt, width=70)
    return out

def get_media_transcription(job_name):
    logger.info(f"get_media_transcription({job_name})")
    try:
        response = TRANSCRIBE.get_transcription_job(TranscriptionJobName=job_name)
    except Exception as e:
        logger.error("Exception getting transription job: " + job_name)
        logger.error(e)
        return None
    #logger.info("response: " + json.dumps(response, default=str))
    return response


# jobcompete handler - this lambda processes and indexes a single media file transcription
# invoked by EventBridge trigger as the Amazon Transcribe job for each media file (started by the crawler lambda) completes
def lambda_handler(event, context):
    logger.info("Received event: %s" % json.dumps(event))
    # get results of Amazon Transcribe job, identified in the payload of the JobCompletion event
    response = get_media_transcription(event['detail']['TranscriptionJobName'])
    if response and ('TranscriptionJob' in response):
        file_uri = response['TranscriptionJob']['Transcript']['TranscriptFileUri']
        text = prepare_transcript(file_uri)
        media_file_uri = response['TranscriptionJob']['Media']['MediaFileUri']
        put_document(dsId=DS_ID, indexId=INDEX_ID, s3url=media_file_uri, text=text)
        stop_kendra_sync_job_when_all_done(dsId=DS_ID, indexId=INDEX_ID)
    else:
        logger.error("Unable to retrieve transcription from job.")
        
    