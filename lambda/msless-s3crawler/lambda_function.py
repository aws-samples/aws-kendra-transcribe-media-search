import os
import json
import sys

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

import logging
import cfnresponse

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

def start_media_sync_job():
    resp = kendra.list_data_source_sync_jobs(Id=ds_id, IndexId=index_id)
    #logger.info("Sync Jobs:" + json.dumps(resp))
    if ('History' in resp):
        for h in resp['History']:
            if (h['Status'] in ['SYNCING', 'SYNCING_INDEXING']):
                logger.info('Previous sync job still running')
                return({'ExecutionId': 'STILL_SYNCING'})
    return kendra.start_data_source_sync_job(Id=ds_id, IndexId=index_id)

def get_file_name(s3url):
    url_split = s3url.split('/')
    url_len = len(url_split)
    return url_split[url_len-1]

def delete_media_transcription(job_uri):
    job_name = stack_name + '-' + get_file_name(job_uri)
    try:
        transcribe.delete_transcription_job(TranscriptionJobName=job_name)
    except ClientError as e:
        logger.info("Exception while deleting: " + job_name + "Error:" + e.response['Error']['Message'])

def start_media_transcription(job_uri):
    job_name = stack_name + '-' + get_file_name(job_uri)
    logger.info("Starting media transcription job:" + job_name)
    try:
        response = transcribe.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': job_uri},
            IdentifyLanguage=True
        )
    except ClientError as e:
        logger.info("Exception while starting: " + job_name + "Error:" + e.response['Error']['Message'])
        return {'Error': e.response['Error']['Message'] }
    return response

def restart_media_transcription(job_uri):
    delete_media_transcription(job_uri)
    return start_media_transcription(job_uri)

def get_s3file(s3url):
    try:
        response = table.get_item(Key={'s3url': s3url})
    except ClientError as e:
        logger.info(e.response['Error']['Message'])
        return {'s3url': "NULL" }
    if ('Item' in response):
        return response['Item']
    else:
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

def get_object_list(bucket_name, folder_prefix):
    try:
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=folder_prefix)
    except ClientError as e:
        logger.info(e.response['Error']['Message'])
        logger.info('Exception on: ' + bucket_name + '/' + folder_prefix)
        return {'Error': e.response['Error']['Message'] }
    return response;

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

def delete_event_handler(event):
    logger.info("At this time there is nothing to be done to handle the Delete event")
    
def lambda_handler(event, context):
    logger.info("Received event: %s" % json.dumps(event))
    if (('RequestType' in event) and (event['RequestType'] == 'Delete')):
        delete_event_handler(event)
        logger.info("Return with success from a Delete event")
        status = cfnresponse.SUCCESS
        cfnresponse.send(event, context, status, {}, None)
        return status
        
    #Now this is not a delete event
    r = get_s3file(stack_name)
    if (r['s3url'] != "NULL"):
        if (r['sync_state'] == "RUNNING"):
            logger.info("Previous lambda still running. Exiting with SUCCESS")
            status = cfnresponse.SUCCESS
            if ('ResourceType' in event):
                if (event['ResourceType'].find('CustomResource') > 0):
                    logger.info("cfnresponse:" + "SUCCESS")
                    cfnresponse.send(event, context, status, {}, None)
            return status
    put_s3file(stack_name, '', '', '', 'RUNNING')            
    j = start_media_sync_job()
    if (j['ExecutionId'] == 'STILL_SYNCING'):
        logger.info("Previous sync job still running. Exiting")
        status = cfnresponse.FAILED
        if ('ResourceType' in event):
            if (event['ResourceType'].find('CustomResource') > 0):
                logger.info("cfnresponse:" + "FAILED")
                cfnresponse.send(event, context, status, {}, None)
        put_s3file(stack_name, '', '', '', 'STOPPED')            
        return status
    else:
        response = get_object_list(bucket_name, folder_prefix)
        if ('Error' in response):
            logger.info("Exiting due to error in get_object_list:" + response['Error'])
            status = cfnresponse.FAILED
            if ('ResourceType' in event):
                if (event['ResourceType'].find('CustomResource') > 0):
                    logger.info("cfnresponse:" + "FAILED")
                    cfnresponse.send(event, context, status, {}, None)
            put_s3file(stack_name, '', '', '', 'STOPPED')            
            stop_media_sync_job_when_all_done()
            return status
        for r in response['Contents']:
            #At this time we deal with only mp3 and mp4 files
            if (r['Key'].endswith('.mp3') or r['Key'].endswith('.mp4')):
                s3url = "s3://" + bucket_name + '/' + r['Key']
                lastModified = r['LastModified'].strftime("%m:%d:%Y:%H:%M:%S")
                item = get_s3file(s3url)
                if (item['s3url'] == "NULL"):
                    resp = start_media_transcription(s3url)
                    if ('TranscriptionJob' in resp):
                        put_s3file(s3url, lastModified, "New", j['ExecutionId'], "RUNNING")
                    logger.info("New:" + s3url)
                elif (lastModified == item['lastModified']):
                    resp = put_s3file(s3url, lastModified, "Active-current","NONE", "DONE")
                    logger.info("Active-current:" + s3url)
                else:
                    resp = restart_media_transcription(s3url)
                    if ('TranscriptionJob' in resp):
                        put_s3file(s3url, lastModified, "Modified", j['ExecutionId'], "RUNNING")
                    logger.info("Modified:" + s3url)
            else:
                logger.info("File type not supported. Skipping: " + r['Key'])
        status = cfnresponse.SUCCESS
        if ('ResourceType' in event):
            if (event['ResourceType'].find('CustomResource') > 0):
                logger.info("cfnresponse:" + "SUCCESS")
                cfnresponse.send(event, context, status, {}, None)
        put_s3file(stack_name, '', '', '', 'STOPPED')            
        #Call stop media sync to handle the case where no new jobs were created
        stop_media_sync_job_when_all_done()
        return status