# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import os
import sys
import json
import boto3
import logging
import cfnresponse

from urllib.parse import urlparse
from urllib.parse import parse_qs

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

sys.path.insert(1, '/tmp/')
from pytube import YouTube
from pytube import Playlist
dynamodb = boto3.resource('dynamodb')
ytcommonURL='https://www.youtube.com/watch?v='

# Media Bucket details
region = os.environ['AWS_REGION']
mediaBucket = os.environ['mediaBucket']

# Where to save
SAVE_PATH = "/tmp"
mediaFolderPrefix = os.environ['mediaFolderPrefix']
metaDataFolderPrefix = os.environ['metaDataFolderPrefix']+mediaFolderPrefix


def ytvideoid(value):
    query = urlparse(value)
    if query.hostname == 'youtu.be':
        return query.path[1:]
    if query.hostname in ('www.youtube.com', 'youtube.com'):
        if query.path == '/watch':
            p = parse_qs(query.query)
            return p['v'][0]
        if query.path[:7] == '/embed/':
            return query.path.split('/')[2]
        if query.path[:3] == '/v/':
            return query.path.split('/')[2]
    return None

def exit_status(event, context, status):
    logger.info(f"exit_status({status})")
    if ('ResourceType' in event):
        if (event['ResourceType'].find('CustomResource') > 0):
            logger.info("cfnresponse:" + status)
            cfnresponse.send(event, context, status, {}, None)
    return status

def downloadYTAudio(event,context,ytkey,url):
    ytVideoURL = ytcommonURL+ytkey
    yt = YouTube(ytVideoURL)
    logger.info('Downloading Youtube Audio for ->'+ytVideoURL)
    audio_name=ytkey
    try:
        yt.streams.filter(only_audio=True).first().download(SAVE_PATH,audio_name+'.mp3')
        logger.info(yt.metadata)
    except Exception as e:
        statusCode=500
        body='ERROR: Could not download Audio from YouTube->'+str(e)
        logger.error(body)
        return
    try:
        s3_client = boto3.client('s3', region) 
        logger.info('Uploading to s3 media bucket ->'+mediaFolderPrefix+audio_name+'.mp3')
        s3_client.upload_file(SAVE_PATH+'/'+audio_name+'.mp3', mediaBucket, mediaFolderPrefix+audio_name+'.mp3')
    except Exception as e:
        body='ERROR: Could not upload Audio to S3->'+str(e)
        logger.error(body)
        return
    try:
        updateDDBTable(event,context,ytkey, yt.author, yt.length, yt.publish_date, yt.views,ytVideoURL, yt.title,url)
    except Exception as e:
        body='ERROR: Could not update DynamoDB table YTMediaDDBQueueTable->'+str(e)
        logger.error(body)    

def updateDDBTable(event,context,ytkey,author,video_length,publish_date,view_count,source_uri,title,url):
    tableName = os.environ['ddbTableName']
    table = dynamodb.Table(tableName)
    try:
        response = table.put_item(
                Item={
                        'ytkey': ytkey,
                        'downloaded': True,
                        'ytauthor': author,
                        'video_length': video_length,
                        'publish_date': publish_date.isoformat(),
                        'view_count': view_count,
                        'source_uri': source_uri,
                        'title': title 
                    },
                    ConditionExpression='attribute_not_exists(ytkey)'
            )            
    except Exception as e:
        if e.response['Error']['Code']=='ConditionalCheckFailedException':  
            logger.info("Youtube Video "+url+" has already been indexed")
        else:
            logger.error('ERROR: Could not index video '+url+' ->'+str(e))
            return exit_status(event, context, cfnresponse.FAILED)

    try:
        json_dump = json.dumps({'Attributes': {'_source_uri':source_uri, '_created_at':publish_date.isoformat(),'video_length':video_length,'video_view_count':view_count,'ytauthor':author,'ytsource':source_uri},
                            'Title': title
                            })
        encoded_string = json_dump.encode("utf-8")
        file_name = metaDataFolderPrefix+ytkey+".mp3.metadata.json"
        s3_path = file_name
        s3 = boto3.resource("s3")
        logger.info('Uploading to s3 media bucket ->'+metaDataFolderPrefix+ytkey+'.mp3.metadata.json')
        s3.Bucket(mediaBucket).put_object(Key=s3_path, Body=encoded_string)
    except Exception as e:
        logger.error("Could not upload the metadata json to S3" + str(e) )

def lambda_handler(event, context):
    # Handle Delete event from Cloudformation custom resource
    # In all other cases start crawler
    if (('RequestType' in event) and (event['RequestType'] == 'Delete')):
        logger.info("Cfn Delete event - no action - return Success")
        return exit_status(event, context, cfnresponse.SUCCESS)
        
    playListURL = os.environ['playListURL']
    region = os.environ['AWS_REGION']
    numberOfYTVideos = int(os.environ['numberOfYTVideos'])
    
    ytPlayList = Playlist(playListURL)
    logger.info('Number of videos in the playlist->'+str(len(ytPlayList.video_urls)))
    endoflist=len(ytPlayList.video_urls) if len(ytPlayList.video_urls)<numberOfYTVideos else numberOfYTVideos
    
    for url in ytPlayList.video_urls[:endoflist]:     
        logger.info("Checking Youtube Video "+url)
        videoid=ytvideoid(url)
        try:
            downloadYTAudio(event,context,videoid,url)
        except Exception as e:
            logger.error('ERROR: Could not index video '+url+' ->'+str(e))
            return exit_status(event, context, cfnresponse.FAILED)

    return exit_status(event, context, cfnresponse.SUCCESS)