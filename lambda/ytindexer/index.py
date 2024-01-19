# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import os
import sys
import json
import boto3
import logging
import cfnresponse
import time
import yt_dlp
from datetime import datetime
from botocore.exceptions import ClientError

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

# Number of retries for downloading YT videos
retryceil=int(os.environ['RETRY'])

sys.path.insert(1, '/tmp/')

dynamodb = boto3.resource('dynamodb')
ytcommonURL='https://www.youtube.com/watch?v='

# Media Bucket details
region = os.environ['AWS_REGION']
mediaBucket = os.environ['mediaBucket']

# Where to save
SAVE_PATH = "/tmp"
mediaFolderPrefix = os.environ['mediaFolderPrefix']
metaDataFolderPrefix = os.environ['metaDataFolderPrefix']+mediaFolderPrefix

def exit_status(event, context, status):
    logger.info(f"exit_status({status})")
    if ('ResourceType' in event):
        if (event['ResourceType'].find('CustomResource') > 0):
            logger.info("cfnresponse:" + status)
            if ('PhysicalResourceId' in event):
                resid=event['PhysicalResourceId']
                cfnresponse.send(event, context, status, {}, resid)
            else:
               cfnresponse.send(event, context, status, {}, None)
    return status

def updateDDBTable(videoMetaData):    
    tableName = os.environ['ddbTableName']
    table = dynamodb.Table(tableName)
    for video in videoMetaData['entries']:
        video_id = video['id']
        title = video['title']
        uploader = video['uploader'] 
        upload_date = video['upload_date']
        duration = video['duration']
        view_count = video['view_count']  
        date_obj = datetime.strptime(upload_date, "%Y%m%d")
        iso_upload_date = date_obj.isoformat()

        try:
            response = table.put_item(
                    Item={
                            'ytkey': video_id,
                            'downloaded': True,
                            'ytauthor': uploader,
                            'video_length': duration,
                            'publish_date': iso_upload_date,
                            'view_count': view_count,
                            'source_uri': ytcommonURL+video_id,
                            'title': title 
                        },
                        ConditionExpression='attribute_not_exists(ytkey)'
                )            
        except ClientError as e:
            if e.response['Error']['Code']=='ConditionalCheckFailedException':  
                logger.info("Youtube Video "+ytcommonURL+video_id+" has already been indexed")
        except Exception as e:
            logger.error('ERROR: Could not index video '+ytcommonURL+video_id+' ->'+str(e))
            return 1
        try:
            json_dump = json.dumps({'Attributes': {'_source_uri':ytcommonURL+video_id, '_category':'YouTube video', '_created_at':iso_upload_date,'video_length':duration,'video_view_count':view_count,'ytauthor':uploader,'ytsource':ytcommonURL+video_id},
                                'Title': title
                                })
            encoded_string = json_dump.encode("utf-8")
            file_name = metaDataFolderPrefix+video_id+".mp3.metadata.json"
            s3_path = file_name
            s3 = boto3.resource("s3")
            logger.info('Uploading to s3 media bucket ->'+metaDataFolderPrefix+video_id+'.mp3.metadata.json')
            s3.Bucket(mediaBucket).put_object(Key=s3_path, Body=encoded_string)
        except Exception as e:
            logger.error("Could not upload the metadata json to S3" + str(e) )
            return 2
    return 0

def empty_bucket(mediaBucket,event, context):
    if mediaBucket:
        try:
            s3 = boto3.resource('s3')
            bucket = s3.Bucket(mediaBucket)
            bucket.objects.all().delete()
        except Exception as e:
            logger.info("Exception while deleting files ->"+str(e))
            return exit_status(event, context, cfnresponse.FAILED)

def lambda_handler(event, context):
    # Handle Delete event from Cloudformation custom resource
    # In all other cases start crawler
    logger.info("event->"+str(event))
    if (('RequestType' in event) and (event['RequestType'] == 'Delete')):
        # Empty Bucket before delete
        empty_bucket(mediaBucket,event, context)
        logger.info("Cfn Delete event - no action - return Success")
        return exit_status(event, context, cfnresponse.SUCCESS)
        
    playListURL = os.environ['playListURL']
    if not playListURL:
        logger.info("Play List URL is empty. Exiting - return Success")
        return exit_status(event, context, cfnresponse.SUCCESS)

    region = os.environ['AWS_REGION']
    numberOfYTVideos = int(os.environ['numberOfYTVideos'])
    ydl_opts = {
            'format': 'bestaudio',
            'cachedir': SAVE_PATH,
            'outtmpl': SAVE_PATH+'/%(id)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'playlistend': numberOfYTVideos
            }
            
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([playListURL])
        except Exception as e:
            logger.error('ERROR: Could not index videos from playlist ->'+str(e))
            return exit_status(event, context, cfnresponse.FAILED)
        try:
            videoMetaData = ydl.extract_info(playListURL, download=False)
        except Exception as e:
            logger.error('ERROR: Could not extract metadata from YT videos ->'+str(e))
            return exit_status(event, context, cfnresponse.FAILED)
        for video in videoMetaData['entries']:
            video_id = video['id']
            # Upload downloaded mp3 files to S3
            try:
                s3_client = boto3.client('s3', region) 
                logger.info('Uploading to s3 media bucket ->'+mediaFolderPrefix+video_id+'.mp3')
                s3_client.upload_file(SAVE_PATH+'/'+video_id+'.mp3', mediaBucket, mediaFolderPrefix+video_id+'.mp3')
            except Exception as e:
                body='ERROR: Could not upload Audio to S3->'+str(e)
                logger.error(body)
            # Update the DynamoDB table    
            try:
                returnVal = updateDDBTable(videoMetaData)
            except Exception as e:
                logger.error('ERROR: Could not update DDB'+str(e))
                return exit_status(event, context, cfnresponse.FAILED)
            
    return exit_status(event, context, cfnresponse.SUCCESS)