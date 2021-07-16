import os
import json
import sys
import textwrap
import urllib

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

import logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
bucket_name = os.environ['MEDIA_BUCKET']
folder_prefix = os.environ['MEDIA_FOLDER_PREFIX']


s3crawl_table = os.environ['MEDIA_FILE_TABLE']
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(s3crawl_table)

transcribe = boto3.client('transcribe')
kendra = boto3.client('kendra')
index_id = os.environ['INDEX_ID']
ds_id = os.environ['DS_ID']
stack_name = os.environ['STACK_NAME']

def get_s3file(s3url):
    logger.info("get_s3file: " + s3url)
    try:
        response = table.get_item(Key={'s3url': s3url})
    except ClientError as e:
        logger.info("Exception:" + e.response['Error']['Message'])
        return {'s3url': "NULL" }
    if ('Item' in response):
        logger.info("Return: " + json.dumps(response['Item']))
        return response['Item']
    else:
        logger.info("Item not in response. Returning NULL:" + json.dumps(response))
        return {'s3url': "NULL"}

def put_s3file(s3url, lastModified, status, execution_id, sync_state):
    response = table.put_item(
       Item={
            's3url': s3url,
            'lastModified': lastModified,
            'status': status,
            'execution_id': execution_id,
            'sync_state': sync_state
        }
    )
    return response

def stop_media_sync_job_when_all_done():
    response = table.scan(
                Select="COUNT",
                FilterExpression=Attr('sync_state').eq('RUNNING'),
                TableName=s3crawl_table
            )
    if (response['Count'] == 0):
        #All DONE
        logger.info("This was the last Transcribe job. Stop Data Source Sync.")
        kendra.stop_data_source_sync_job(Id=ds_id, IndexId=index_id)
    else:
        logger.info("Wait for all Transcribe jobs to complete")

def put_document(file_name, file_text):
    logger.info("put_document:" + file_name)
    logger.info("put_document file_text:" + file_text)
    s3url = "s3://" + bucket_name + '/' + folder_prefix + file_name
    fobject = get_s3file(s3url)
    logger.info("put_document fobject:" + json.dumps(fobject))
    if (fobject['s3url'] != "NULL"):
        documents = [ 
            {
                "Id": file_name,
                "Title": file_name,
                "Blob": file_text,
                "Attributes": [
                    {
                        "Key": "_data_source_id",
                        "Value": {
                            "StringValue": ds_id
                        }
                    },
                    {
                        "Key": "_data_source_sync_job_execution_id",
                        "Value": {
                            "StringValue": fobject['execution_id']
                        }
                    },
                    {
                        "Key": "_source_uri",
                        "Value": {
                            "StringValue": "https://s3.us-east-1.amazonaws.com/" + bucket_name + '/' + folder_prefix + file_name
                        }
                    }
                ]
            }
        ]
    
        result = kendra.batch_put_document(
            IndexId = index_id,
            Documents = documents
        )
        logger.info("kendra.batch_put_document: " + json.dumps(documents))
        logger.info("result: " + json.dumps(result))
        put_s3file(s3url, fobject['lastModified'], fobject['status'], "NONE", "DONE")

def transcript_processor(furl):
    response = urllib.request.urlopen(furl)
    jobject = json.loads(response.read())
    items = jobject["results"]["items"]
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
    try:
        response = transcribe.get_transcription_job(TranscriptionJobName=job_name)
    except ClientError as e:
        logger.info("Exception while getting: " + job_name + "Error:" + e.response['Error']['Message'])
        response = e
    return response

def get_file_name(s3url):
    url_split = s3url.split('/')
    url_len = len(url_split)
    return url_split[url_len-1]
    
def lambda_handler(event, context):
    logger.info("Got:" + json.dumps(event))
    r = get_media_transcription(event['detail']['TranscriptionJobName'])
    if ('TranscriptionJob' in r):
        file_uri = r['TranscriptionJob']['Transcript']['TranscriptFileUri']
        logger.info("File URI:" + file_uri)
        text = transcript_processor(file_uri)
        logger.info("Text: " + text)
        media_file_uri = r['TranscriptionJob']['Media']['MediaFileUri']
        file_name = get_file_name(media_file_uri)
        put_document(file_name, text)
        stop_media_sync_job_when_all_done()
    else:
        logger.info("Did not get file uri")
    