# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import re
import time
import cfnresponse

# Media file suffixes must match one of the supported file types
SUPPORTED_MEDIA_TYPES = ["mp3","mp4","wav","flac","ogg","amr","webm"]

from common import logger
from common import MEDIA_BUCKET, MEDIA_FOLDER_PREFIX, INDEX_ID, DS_ID, STACK_NAME, TRANSCRIBE_ROLE
from common import S3, TRANSCRIBE
from common import start_kendra_sync_job, stop_kendra_sync_job_when_all_done, process_deletions
from common import get_crawler_state, put_crawler_state, get_file_status, put_file_status

# generate a unique job name for transcribe satisfying the naming regex requirements 
def transcribe_job_name(*args):
    timestamp=time.time()
    job_name = "__".join(args) + "_" + str(timestamp)
    job_name = re.sub(r"[^0-9a-zA-Z._-]+","--",job_name)
    return job_name

def start_media_transcription(name, job_uri, role):
    logger.info(f"start_media_transcription(name={name}, job_uri={job_uri}, role={role})")
    job_name = transcribe_job_name(name, job_uri)
    logger.info("Starting media transcription job:" + job_name)
    try:
        response = TRANSCRIBE.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': job_uri},
            IdentifyLanguage=True,
            JobExecutionSettings={
                'AllowDeferredExecution': True,
                'DataAccessRoleArn': role
            }
        )
    except Exception as e:
        logger.error("Exception while starting: " + job_name)
        logger.error(e)
        return False
    return job_name

def restart_media_transcription(name, job_uri, role):
    logger.info(f"restart_media_transcription(name={name}, job_uri={job_uri}, role={role})")
    return start_media_transcription(name, job_uri, role)

def process_s3_media_object(crawlername, bucketname, s3object, kendra_sync_job_id, role):
    s3url = "s3://" + bucketname + '/' + s3object['Key']
    logger.info(f"process_s3_media_object() - Key: {s3url}")
    lastModified = s3object['LastModified'].strftime("%m:%d:%Y:%H:%M:%S")
    size_bytes = s3object['Size']
    item = get_file_status(s3url)
    job_name=None
    if (item == None or item.get("status") == "DELETED"):
        logger.info("NEW:" + s3url)
        job_name = start_media_transcription(crawlername, s3url, role)
        if job_name:
            put_file_status(
                s3url, lastModified, size_bytes, duration_secs=None, status="ACTIVE-NEW", 
                transcribe_job_id=job_name, transcribe_state="RUNNING", transcribe_secs=None, 
                sync_job_id=kendra_sync_job_id, sync_state="RUNNING"
                )
    elif (lastModified != item['lastModified']):
        logger.info("MODIFIED:" + s3url)
        job_name = restart_media_transcription(crawlername, s3url, role)
        if job_name:
            put_file_status(
                s3url, lastModified, size_bytes, duration_secs=None, status="ACTIVE-MODIFIED", 
                transcribe_job_id=job_name, transcribe_state="RUNNING", transcribe_secs=None,
                sync_job_id=kendra_sync_job_id, sync_state="RUNNING"
                )
    else:
        logger.info("UNCHANGED:" + s3url)
        put_file_status(
            s3url, lastModified, size_bytes, duration_secs=item['duration_secs'], status="ACTIVE-UNCHANGED", 
            transcribe_job_id=item['transcribe_job_id'], transcribe_state="DONE", transcribe_secs=item['transcribe_secs'],
            sync_job_id=item['sync_job_id'], sync_state="DONE"
            )
    return s3url

def list_s3_media_objects(bucketname, prefix):
    logger.info(f"list_s3_media_objects({bucketname},{prefix})")
    s3mediaobjects=[]
    paginator = S3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucketname, Prefix=prefix)
    for page in pages:
        if "Contents" in page:
            for s3object in page["Contents"]:
                suffix = s3object['Key'].rsplit(".",1)[-1]
                if suffix.upper() in (mediatype.upper() for mediatype in SUPPORTED_MEDIA_TYPES):
                    logger.info("File type supported. Adding: " + s3object['Key'])
                    s3mediaobjects.append(s3object)
                else:
                    logger.info("File type not supported. Skipping: " + s3object['Key'])
        else:
            logger.info(f"No files found in {bucketname}/{prefix}")
    return s3mediaobjects

def exit_status(event, context, status):
    logger.info(f"exit_status({status})")
    if ('ResourceType' in event):
        if (event['ResourceType'].find('CustomResource') > 0):
            logger.info("cfnresponse:" + status)
            cfnresponse.send(event, context, status, {}, None)
    return status       
    
def lambda_handler(event, context):
    logger.info("Received event: %s" % json.dumps(event))
    
    # Handle Delete event from Cloudformation custom resource
    # In all other cases start crawler
    if (('RequestType' in event) and (event['RequestType'] == 'Delete')):
        logger.info("Cfn Delete event - no action - return Success")
        return exit_status(event, context, cfnresponse.SUCCESS)
    
    # exit if crawler is already running
    crawler_state = get_crawler_state(STACK_NAME)
    if (crawler_state):
        logger.info(f"crawler sync state: {crawler_state}")
        if (crawler_state == "RUNNING"):
            logger.info("Previous crawler invocation is running. Exiting")
            return exit_status(event, context, cfnresponse.SUCCESS)
            
    # Start crawler, and set status in DynamoDB table
    logger.info("** Start crawler **")
    kendra_sync_job_id = start_kendra_sync_job(dsId=DS_ID, indexId=INDEX_ID)
    if (kendra_sync_job_id == None):
        logger.info("Previous sync job still running. Exiting")
        return exit_status(event, context, cfnresponse.SUCCESS)
    put_crawler_state(STACK_NAME,'RUNNING')  
        
    # process S3 media objects
    s3files=[]
    try:
        logger.info("** List and process S3 media objects **")
        s3mediaobjects = list_s3_media_objects(MEDIA_BUCKET, MEDIA_FOLDER_PREFIX)
        for s3mediaobject in s3mediaobjects:
            s3url=process_s3_media_object(STACK_NAME, MEDIA_BUCKET, s3mediaobject, kendra_sync_job_id, TRANSCRIBE_ROLE)
            s3files.append(s3url)
        # detect and delete indexed docs where files that are no longer in the source bucket location
        # reasons: file deleted, or indexer config updated to crawl a new location
        logger.info("** Process deletions **")
        process_deletions(DS_ID, INDEX_ID, kendra_sync_job_id=kendra_sync_job_id, s3files=s3files)
    except Exception as e:
        logger.error("Exception: " + str(e))
        put_crawler_state(STACK_NAME, 'STOPPED')            
        stop_kendra_sync_job_when_all_done(dsId=DS_ID, indexId=INDEX_ID)
        return exit_status(event, context, cfnresponse.FAILED)

    # Stop crawler
    logger.info("** Stop crawler **")
    put_crawler_state(STACK_NAME, 'STOPPED')
    
    # Stop media sync job if no new transcription jobs were started
    stop_kendra_sync_job_when_all_done(dsId=DS_ID, indexId=INDEX_ID)
    
    # All done
    return exit_status(event, context, cfnresponse.SUCCESS)
    
    
    
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    lambda_handler({},{})
    
    
    