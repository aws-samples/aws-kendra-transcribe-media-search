import os
import boto3
import json
import time
from boto3.dynamodb.conditions import Key, Attr

import logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
MEDIA_BUCKET = os.environ['MEDIA_BUCKET']
MEDIA_FOLDER_PREFIX = os.environ['MEDIA_FOLDER_PREFIX']
INDEX_ID = os.environ['INDEX_ID']
DS_ID = os.environ['DS_ID']
STACK_NAME = os.environ['STACK_NAME']
MEDIA_FILE_TABLE = os.environ['MEDIA_FILE_TABLE']
TRANSCRIBE_ROLE = os.environ['TRANSCRIBE_ROLE']

# AWS clients
S3 = boto3.client('s3')
TRANSCRIBE = boto3.client('transcribe')
KENDRA = boto3.client('kendra')
DYNAMODB = boto3.resource('dynamodb')
TABLE = DYNAMODB.Table(MEDIA_FILE_TABLE)

# Common functions
def start_kendra_sync_job(dsId, indexId):
    logger.info(f"start_kendra_sync_job(dsId={dsId}, indexId={indexId})")
    # If all jobs are done ensure sync job is stopped.
    stop_kendra_sync_job_when_all_done(dsId=DS_ID, indexId=INDEX_ID)
    # Check if sync job is still running
    resp = KENDRA.list_data_source_sync_jobs(Id=dsId, IndexId=indexId)
    if ('History' in resp):
        for h in resp['History']:
            if (h['Status'] in ['SYNCING', 'SYNCING_INDEXING']):
                return None
    # No running sync job - we will start one.
    return KENDRA.start_data_source_sync_job(Id=dsId, IndexId=indexId)

def stop_kendra_sync_job_when_all_done(dsId, indexId):
    logger.info(f"stop_kendra_sync_job_when_all_done(dsId={dsId}, indexId={indexId})")
    response = TABLE.scan(
                Select="COUNT",
                FilterExpression=Attr('sync_state').eq('RUNNING')
            )
    logger.info("DynamoDB scan result: " + json.dumps(response))
    if (response['Count'] == 0):
        #All DONE
        logger.info("No media files currently being transcribed. Stop Data Source Sync.")
        KENDRA.stop_data_source_sync_job(Id=dsId, IndexId=indexId)
        time.sleep(10)  # wait a few seconds for sync job to stop
    else:
        logger.info(f"Wait for remaining Transcribe jobs to complete - count: {response['Count']}")
    return True
    
def get_crawler_state(name):
    logger.info(f"get_crawler_state({name})")
    item = get_statusTableItem(name)
    if item and 'crawler_state' in item:
        return item['crawler_state']
    return None
    
def get_file_status(s3url):
    logger.info(f"get_file_status({s3url})")
    return get_statusTableItem(s3url)

# Currently we use same DynamoDB table to track status of indexer (id=stackname) as well as each S3 media file (id=s3url)
def get_statusTableItem(id):
    item=None
    try:
        response = TABLE.get_item(Key={'id': id})
    except Exception as e:
        logger.error(e)
        return None
    if ('Item' in response):
        item = response['Item']
    logger.info("response item: " + json.dumps(item))
    return item


def put_crawler_state(name, status):
    logger.info(f"put_crawler_status({name}, status={status})")
    return put_statusTableItem(s3url=name, crawler_state=status)
    
def put_file_status(s3url, lastModified, status, transcribe_job_id, transcribe_state, sync_job_id, sync_state):
    logger.info(f"put_file_status({s3url}, lastModified={lastModified}, status={status}, transcribe_job_id={transcribe_job_id}, transcribe_state={transcribe_state}, sync_job_id={sync_job_id}, sync_state={sync_state})")
    return put_statusTableItem(s3url, lastModified, status, transcribe_job_id, transcribe_state, sync_job_id, sync_state)

# Currently use same DynamoDB table to track status of indexer (id=stackname) as well as each S3 media file (id=s3url)
def put_statusTableItem(s3url, lastModified='', status='', transcribe_job_id='', transcribe_state='', sync_job_id='', sync_state='', crawler_state=''):
    response = TABLE.put_item(
       Item={
            'id': s3url,
            'lastModified': lastModified,
            'status': status,
            'transcribe_job_id': transcribe_job_id,
            'transcribe_state': transcribe_state,
            'sync_job_id': sync_job_id,
            'sync_state': sync_state,
            'crawler_state': crawler_state
        }
    )
    return response

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    lambda_handler({},{})