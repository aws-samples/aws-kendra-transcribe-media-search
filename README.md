# This is the code for the Media Serverless Search project

This solution makes audio and video media content searchable in an Amazon Kendra index.
The user provides the details of an Amazon S3 bucket containing the media files to this solution. The solution uses Amazon Transcribe to create a time marked transcript of the solution. This transcript is ingested in an Amazon Kendra search index. A web application is provided to run search queries to the Amazon Kendra index. The application uses the time markers from the search result excerpts to start the media players at the offset of the result.

This project was bootstrapped with [Kendra Samples Application](https://kendrasamples.s3.amazonaws.com/kendrasamples.zip).
A version of this code modified to support audio and video files is included in the src directory

## Architecture
For an architecture diagram please refer to Media Search Architecture.pptx

## Web Client Application

This is found in the src directory. It needs to be built as Amplify Console application.

## Lambda functions

The lambda directory contains two lambda functions that implement the workflow to crawl the S3 bucket with the media files, transcribe them and ingest them in the Kendra index.

### msless-s3crawler
This Lambda function crawls the S3 bucket and starts transribe jobs for new or modified media files.

### msless-jobcomplete-handler
This Lambda funtion gets called for each job completion. It processes the Transcribe output, generates time marked transcript and ingests in Amazon Kendra index.

## CloudFormation Template

The cfn-templates directory contains a CloudFormation template used to deploy the infrastructure - Kendra index, DynamoDB table to keep track of the state of media files, Lambda functions, IAM roles, EventBridge Events etc..

## Learn More

You can learn more in the quip document [Making your audio and video files searchable using Amazon Transcribe and Amazon Kendra](https://quip-amazon.com/aiqzA4WC62jk/Making-your-audio-and-video-files-searchable-using-Amazon-Transcribe-and-Amazon-Kendra).