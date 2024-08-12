# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import logging
import boto3
import cfnresponse
import os
import time
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)
bc = boto3.client('codebuild')
cb_projectname = os.environ['CB_PROJECTNAME']

def lambda_handler(event, context):
    logger.info("Received event: %s" % json.dumps(event))
    
    if 'RequestType' in event and event['RequestType'] != 'Delete':
        try:
            logger.info(f"Calling start_build for CodeBuild Project: {cb_projectname}")
            response = bc.start_build(projectName=cb_projectname)
            logger.info("Returned from start_build")
            
            build_id = response['build']['id']
            wait_time = 5  # Initial wait time in seconds
            max_attempts = 30  # Maximum number of attempts
            
            for attempt in range(max_attempts):
                try:
                    response = bc.batch_get_builds(ids=[build_id])
                    build_status = response['builds'][0]['buildStatus']
                    
                    if build_status == 'SUCCEEDED':
                        logger.info("Build SUCCEEDED")
                        cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, None)
                        return cfnresponse.SUCCESS
                    elif build_status in ['FAILED', 'FAULT', 'TIMED_OUT', 'STOPPED']:
                        logger.info(f"Build {build_status}")
                        cfnresponse.send(event, context, cfnresponse.FAILED, {}, None)
                        return cfnresponse.FAILED
                    else:
                        logger.info(f"Build IN_PROGRESS. Attempt {attempt + 1}/{max_attempts}")
                        time.sleep(wait_time)
                        wait_time = min(wait_time * 2, 60)  # Exponential backoff, max 60 seconds
                
                except ClientError as e:
                    if e.response['Error']['Code'] == 'ThrottlingException':
                        logger.warning(f"Rate limit hit. Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                        wait_time = min(wait_time * 2, 60)  # Exponential backoff, max 60 seconds
                    else:
                        raise
            
            logger.warning("Max attempts reached. Build still in progress.")
            cfnresponse.send(event, context, cfnresponse.FAILED, {}, None)
            return cfnresponse.FAILED
        
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
            cfnresponse.send(event, context, cfnresponse.FAILED, {}, None)
            return cfnresponse.FAILED    
    else:
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, None)
        return cfnresponse.SUCCESS
