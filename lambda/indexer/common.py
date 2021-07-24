# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

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
METADATA_FOLDER_PREFIX = os.environ['MEDIA_FOLDER_PREFIX']
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
                logger.info(f"data source sync running - state: {h['Status']}")
                return None
    # No running sync job - we will start one.
    logger.info(f"start data source sync job")
    response = KENDRA.start_data_source_sync_job(Id=dsId, IndexId=indexId)
    logger.info(f"response:" + json.dumps(response))
    return response['ExecutionId']

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
        logger.info(f"Stopping Data Source sync job for data source {dsId}")
        KENDRA.stop_data_source_sync_job(Id=dsId, IndexId=indexId)
        time.sleep(10)  # wait a few seconds for sync job to stop
    else:
        logger.info(f"Wait for remaining Transcribe jobs to complete - count: {response['Count']}")
    return True


def get_s3urls(response):
    s3urls=[]
    for item in response["Items"]:
        s3url = item["id"]
        s3urls.append(s3url)
    return s3urls
    
def get_all_indexed_files():
    logger.info(f"get_all_indexed_files()")
    scan_args={
        "Select":"SPECIFIC_ATTRIBUTES",
        "ProjectionExpression":'id',
        "FilterExpression":Attr('status').ne(None) & Attr('status').ne('DELETED')        
    }
    logger.info("Initial page scan")
    response = TABLE.scan(**scan_args)
    files=get_s3urls(response)
    exclusiveStartKey = response.get("LastEvaluatedKey")
    # handle possible pagination (boto3 paginator not available for table api)
    while exclusiveStartKey:
        logger.info("Subsequent page scan")
        scan_args["ExclusiveStartKey"] = exclusiveStartKey
        response = TABLE.scan(**scan_args)
        exclusiveStartKey = response.get("LastEvaluatedKey")
        files = files + get_s3urls(response)
    return files
    
def batches(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]
        
def delete_kendra_docs(dsId, indexId, kendra_sync_job_id, deletions):
    logger.info(f"delete_kendra_docs(dsId={dsId}, indexId={indexId}, deletions[{len(deletions)} docs..])")
    deletion_batches = list(batches(deletions,10))
    for deletion_batch in deletion_batches:
        try:
            logger.info(f"KENDRA.batch_delete_document - {len(deletion_batch)} documents, first few: {deletion_batch[0:2]}")
            response = KENDRA.batch_delete_document(
                IndexId=indexId,
                DocumentIdList=deletion_batch,
                DataSourceSyncJobMetricTarget={
                    'DataSourceId': dsId,
                    'DataSourceSyncJobId': kendra_sync_job_id
                    }
                )
            if "FailedDocuments" in response:
                for failedDocument in response["FailedDocuments"]:
                    logger.error(f"Failed to delete doc from index: {failedDocument['Id']}. Reason {failedDocument['ErrorMessage']}")
                    put_statusTableItem(id=failedDocument['Id'], status="DELETED", sync_state="FAILED TO DELETE FROM INDEX")
        except Exception as e:
            logger.error("Exception in KENDRA.batch_delete_document: " + str(e))
            for s3url in deletions:
                put_statusTableItem(id=s3url, status="DELETED", sync_state="FAILED TO DELETE FROM INDEX")
            return False
    return True

def process_deletions(dsId, indexId, kendra_sync_job_id, s3files):
    logger.info(f"process_deleted_files(dsId={dsId}, indexId={indexId}, s3files[])")
    # get list of indexed files from the DynamoDB table
    indexed_files = get_all_indexed_files()
    logger.info(f"s3 file count: {len(s3files)}, first few: {s3files[0:2]}")
    logger.info(f"indexed file count: {len(indexed_files)}, first few: {indexed_files[0:2]}")
    # identify indexed_files not in the list of current s3files
    deletions = list(set(indexed_files) - set((s3files)))
    if deletions:
        logger.info(f"Deleted file count: {len(deletions)}, first few: {deletions[0:2]}...")
        for s3url in deletions:
            put_statusTableItem(id=s3url, status="DELETED", sync_state="DELETED")
        delete_kendra_docs(dsId, indexId, kendra_sync_job_id, deletions)
    else:
        logger.info("No deleted files.. nothing to do")
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
    logger.info("response item: " + json.dumps(item, default=str))
    return item


def put_crawler_state(name, status):
    logger.info(f"put_crawler_status({name}, status={status})")
    return put_statusTableItem(id=name, crawler_state=status)
    
def put_file_status(s3url, lastModified, size_bytes, duration_secs, status,
                    metadata_url, metadata_lastModified,
                    transcribe_job_id, transcribe_state, transcribe_secs, 
                    sync_job_id, sync_state):
    logger.info(f"put_file_status({s3url}, lastModified={lastModified}, size_bytes={size_bytes}, duration_secs={duration_secs}, status={status}, metadata_url={metadata_url}, metadata_lastModified={metadata_lastModified}, transcribe_job_id={transcribe_job_id}, transcribe_state={transcribe_state}, transcribe_secs={transcribe_secs}, sync_job_id={sync_job_id}, sync_state={sync_state})")
    return put_statusTableItem(s3url, lastModified, size_bytes, duration_secs, status, metadata_url, metadata_lastModified, transcribe_job_id, transcribe_state, transcribe_secs, sync_job_id, sync_state)

# Currently use same DynamoDB table to track status of indexer (id=stackname) as well as each S3 media file (id=s3url)
def put_statusTableItem(id, lastModified=None, size_bytes=None, duration_secs=None, status=None, metadata_url=None, metadata_lastModified=None, transcribe_job_id=None, transcribe_state=None, transcribe_secs=None, sync_job_id=None, sync_state=None, crawler_state=None):
    response = TABLE.put_item(
       Item={
            'id': id,
            'lastModified': lastModified,
            'size_bytes': size_bytes,
            'duration_secs': duration_secs,
            'status': status,
            'metadata_url': metadata_url,
            'metadata_lastModified': metadata_lastModified,
            'transcribe_job_id': transcribe_job_id,
            'transcribe_state': transcribe_state,
            'transcribe_secs': transcribe_secs,
            'sync_job_id': sync_job_id,
            'sync_state': sync_state,
            'crawler_state': crawler_state
        }
    )
    return response

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    files=get_all_indexed_files()
    logger.info(len(files))
    for file in files:
        logger.info(file) 
