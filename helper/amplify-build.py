# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
import requests
import argparse
import logging
import os
import time

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)

amplify_client = boto3.client('amplify')


def create_deployment(app_id, branch_name):
    resp = amplify_client.create_deployment(appId=app_id, branchName=branch_name)
    return resp['jobId'],resp['zipUploadUrl']

def upload_payload(upload_url, deployment_loc):
    f = open(deployment_loc, 'rb')
    headers = {"Content-Type": "application/zip"}
    resp = requests.put(upload_url, data=f.read(), headers=headers)
    print(resp)

def start_deployment(app_id, branch_name, job_id):
    resp = amplify_client.start_deployment(appId=app_id, branchName=branch_name, jobId=job_id)
    return resp['jobSummary']['status']

def check_job_status(app_id, branch_name, job_id):
    print(f"Waiting 20 seconds before starting to check job status...")
    time.sleep(20)  # Initial 20-second delay    
    while True:
        response = amplify_client.get_job(appId=app_id, branchName=branch_name, jobId=job_id)
        status = response['job']['summary']['status']
        
        if status == 'SUCCEED':
            print(f"Job {job_id} completed successfully.")
            return True
        elif status == 'FAILED':
            print(f"Job {job_id} failed.")
            return False
        elif status in ['PENDING', 'PROVISIONING', 'RUNNING']:
            print(f"Job {job_id} is still {status}. Waiting...")
            time.sleep(30)  # Wait for 30 seconds before checking again
        else:
            print(f"Unexpected status: {status}. Treating as failure.")
            return False

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AWS Amplify App Build Script')
    parser.add_argument('--app-id', help='Amplify App Name', default='amplify-appid', dest='app_id')
    parser.add_argument('--branch-name', help='Amplify Branch Name', default='main', dest='branch_name')
    parser.add_argument('--dep-loc', help='Deployment package location', default='build.zip', dest='dep_loc')
    args = parser.parse_args()
    
    app_id = args.app_id
    logger.info(f'Application ID: {app_id}')
    if app_id is None:
        exit(1) # App_id is mandatory
   
    job_id, upload_url = create_deployment(app_id=app_id, branch_name=args.branch_name)
    logger.info(f'Job ID: {job_id}')
    logger.info(f'upload_url: {upload_url}')
    logger.info(f'branch_name: {args.branch_name}')

    logger.info(f'Calling upload_payload')
    upload_payload(upload_url=upload_url, deployment_loc=args.dep_loc)
    logger.info(f'Calling start_deployment')
    jobStatus = start_deployment(app_id=app_id, branch_name=args.branch_name, job_id=job_id)
    logger.info(f'Job Status: {jobStatus}')

    # Check job status
    if check_job_status(app_id, args.branch_name, job_id):
        print("Deployment completed successfully.")
        exit(0)
    else:
        print("Deployment failed.")
        exit(1)    



