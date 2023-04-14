# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import boto3
import cfnresponse
import logging
import os

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

def exit_status(event, context, status):
    logger.info(f"exit_status({status})")
    if ('ResourceType' in event):
        if (event['ResourceType'].find('CustomResource') > 0):
            logger.info("cfnresponse:" + status)
            cfnresponse.send(event, context, status, {}, None)
    return status

def empty_bucket(defaultMediaBucket,mediaBucket,event, context):
    if mediaBucket:
        try:
            s3 = boto3.resource('s3')
            bucket = s3.Bucket(defaultMediaBucket)
            bucket.objects.all().delete()
        except Exception as e:
            logger.info("Exception while deleting files >"+str(e))
            return exit_status(event, context, cfnresponse.FAILED)
    

def empty_yt_ddb(yt_ddb,event, context):
    region = os.environ['AWS_REGION']
    dynamodb = boto3.resource('dynamodb', region)
    table = dynamodb.Table(yt_ddb)
    try:
        scan = table.scan(
            ProjectionExpression='#k',
            ExpressionAttributeNames={
                '#k': 'ytkey'
            }
        )
        with table.batch_writer() as batch:
            for each in scan['Items']:
                batch.delete_item(Key=each)
    except Exception as e:
        logger.info("Could not delete item. Error->"+str(e))
        return exit_status(event, context, cfnresponse.FAILED)
    
def lambda_handler(event, context):
    logger.info("Event->"+str(event))
    logger.info("Context->"+str(context))
    if (('RequestType' in event) and (event['RequestType'] == 'Delete')):
        logger.info("Cfn "+event['RequestType']+" event.. Nothing to do..")
        return exit_status(event, context, cfnresponse.SUCCESS)
    if (('RequestType' in event) and (event['RequestType'] == 'Update')):
        logger.info("Cfn "+event['RequestType']+" event. Empty Bucket")
        defaultMediaBucket = event['ResourceProperties']['MediaBucketNameDefault']
        mediaBucket = event['ResourceProperties']['MediaBucketName']
        logger.info("Default Media Bucket->"+defaultMediaBucket)
        logger.info("Media Bucket being passed as parameter->"+mediaBucket)
        empty_bucket(defaultMediaBucket,mediaBucket,event, context)
        yt_ddb = os.environ.get('YTDDB')
        empty_yt_ddb(yt_ddb,event, context)
    return exit_status(event, context, cfnresponse.SUCCESS)