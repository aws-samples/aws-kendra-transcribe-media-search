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
        logger.info(f"Unable to retrieve bucket region (bucket owned by another account?).. defaulting to us-east-1. Bucket: {bucket} - Message: " + str(e))
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
                            "StringValue": file_status['sync_job_id']
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

def get_transcription_job(job_name):
    logger.info(f"get_transcription_job({job_name})")
    try:
        response = TRANSCRIBE.get_transcription_job(TranscriptionJobName=job_name)
    except Exception as e:
        logger.error("Exception getting transription job: " + job_name)
        logger.error(e)
        return None
    logger.info("get_transcription_job response: " + json.dumps(response, default=str))
    return response

def get_transcription_job_duration(transcription_job):
    start_time = transcription_job['TranscriptionJob']['StartTime']
    completion_time = transcription_job['TranscriptionJob']['CompletionTime']
    delta = completion_time - start_time
    return delta.seconds

# jobcompete handler - this lambda processes and indexes a single media file transcription
# invoked by EventBridge trigger as the Amazon Transcribe job for each media file (started by the crawler lambda) completes
def lambda_handler(event, context):
    logger.info("Received event: %s" % json.dumps(event))
    
    job_name = event['detail']['TranscriptionJobName']
    logger.info(f"Transcription job name: {job_name}")
    
    # get results of Amazon Transcribe job
    transcription_job = get_transcription_job(job_name)
    
    if transcription_job == None or ('TranscriptionJob' not in transcription_job):
        logger.error("Unable to retrieve transcription from job.")
    else:
        job_status = transcription_job['TranscriptionJob']['TranscriptionJobStatus']
        media_s3url = transcription_job['TranscriptionJob']['Media']['MediaFileUri']
        item = get_file_status(media_s3url)
        if item == None:
            logger.info("Transcription job for media file not tracked in Indexer Media File table.. possibly this is a job that is not started by MediaSearch indexer")
            return
        if job_status == "FAILED":
            # job failed
            failure_reason = transcription_job['TranscriptionJob']['FailureReason']
            logger.error(f"Transcribe job failed: {job_status} - Reason {failure_reason}")
            put_file_status(
                media_s3url, lastModified=item['lastModified'], size_bytes=item['size_bytes'], status=item['status'], 
                transcribe_job_id=item['transcribe_job_id'], transcribe_state="FAILED", transcribe_secs=None,
                sync_job_id=item['sync_job_id'], sync_state="NOT_SYNCED"
                )            
        else:
            # job completed
            transcript_uri = transcription_job['TranscriptionJob']['Transcript']['TranscriptFileUri']
            transcribe_secs = get_transcription_job_duration(transcription_job)
            # Update transcribe_state
            put_file_status(
                media_s3url, lastModified=item['lastModified'], size_bytes=item['size_bytes'], status=item['status'], 
                transcribe_job_id=item['transcribe_job_id'], transcribe_state="DONE", transcribe_secs=transcribe_secs,
                sync_job_id=item['sync_job_id'], sync_state=item['sync_state']
                )
            try:
                text = prepare_transcript(transcript_uri)
                put_document(dsId=DS_ID, indexId=INDEX_ID, s3url=media_s3url, text=text)
                # Update sync_state
                put_file_status(
                    media_s3url, lastModified=item['lastModified'], size_bytes=item['size_bytes'], status=item['status'], 
                    transcribe_job_id=item['transcribe_job_id'], transcribe_state="DONE", transcribe_secs=transcribe_secs,
                    sync_job_id=item['sync_job_id'], sync_state="DONE"
                    )
            except Exception as e:
                logger.error("Exception thrown during indexing: " + str(e))
                put_file_status(
                    media_s3url, lastModified=item['lastModified'], size_bytes=item['size_bytes'], status=item['status'], 
                    transcribe_job_id=item['transcribe_job_id'], transcribe_state="DONE", transcribe_secs=transcribe_secs, 
                    sync_job_id=item['sync_job_id'], sync_state="FAILED"
                    )
    # Finally, in all cases stop sync job if not more transcription jobs are pending.
    stop_kendra_sync_job_when_all_done(dsId=DS_ID, indexId=INDEX_ID)

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    lambda_handler({"detail":{"TranscriptionJobName":"testjob"}},{})
