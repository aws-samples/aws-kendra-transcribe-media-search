#!/bin/bash
##############################################################################################
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
##############################################################################################

##############################################################################################
# Create new Cfn artifacts bucket if not already existing
# Modify templates to reference new bucket names and prefixes
# create lambda zipfiles with timestamps to ensure redeployment on stack update
# Upload templates to S3 bucket
#
# To deploy to non-default region, set AWS_DEFAULT_REGION to region supported by Amazon Kendra
# See: https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/ - E.g.
# export AWS_DEFAULT_REGION=eu-west-1
##############################################################################################

USAGE="$0 cfn_bucket cfn_prefix [public] [dflt_media_bucket] [dflt_media_prefix] [dflt_metadata_prefix] [dflt_options_prefix]"

BUCKET=$1
[ -z "$BUCKET" ] && echo "Cfn bucket name is required parameter. Usage $USAGE" && exit 1

PREFIX=$2
[ -z "$PREFIX" ] && echo "Prefix is required parameter. Usage $USAGE" && exit 1

ACL=$3
if [ "$ACL" == "public" ]; then
  echo "Published S3 artifacts will be acessible by public (read-only)"
  PUBLIC=true
else
  echo "Published S3 artifacts will NOT be acessible by public."
  PUBLIC=false
fi

SAMPLES_BUCKET=$4
SAMPLES_PREFIX=$5
METADATA_PREFIX=$6
OPTIONS_PREFIX=$7

# Create bucket if it doesn't already exist
aws s3api list-buckets --query 'Buckets[].Name' | grep "\"$BUCKET\"" > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "Creating s3 bucket: $BUCKET"
  aws s3 mb s3://${BUCKET} || exit 1
  aws s3api put-bucket-versioning --bucket ${BUCKET} --versioning-configuration Status=Enabled || exit 1
else
  echo "Using existing bucket: $BUCKET"
fi

# get bucket region for owned accounts
region=$(aws s3api get-bucket-location --bucket $BUCKET --query "LocationConstraint" --output text) || region="us-east-1"
[ -z "$region" -o "$region" == "None" ] && region=us-east-1;
accountid=`aws sts get-caller-identity --query "Account" --output text`

# Assign default values
[ -z "$SAMPLES_PREFIX" ] && SAMPLES_PREFIX="artifacts/mediasearch/sample-media/"
[ -z "$METADATA_PREFIX" ] && METADATA_PREFIX="artifacts/mediasearch/sample-metadata/"

echo -n "Make temp dir: "
timestamp=$(date "+%Y%m%d_%H%M")
tmpdir=/tmp/mediasearch
[ -d /tmp/mediasearch ] && rm -fr /tmp/mediasearch
mkdir -p $tmpdir
pwd

# Config
LAYERS_DIR=$PWD/layers
FINDER_APP_DIR=$PWD/finderapp

echo "Create zipfile for AWS Amplify/CodeCommit"
pushd $FINDER_APP_DIR
finderzip=finder_$timestamp.zip
zip -r $tmpdir/$finderzip ./* -x "node_modules*"
popd

# Install yt_dlp and any other pip layers
echo "------------------------------------------------------------------------------"
echo "Installing Python packages for AWS Lambda Layers if a requirements.txt is present"
echo "------------------------------------------------------------------------------"
if [ -d "$LAYERS_DIR" ]; then
  LAYERS=$(ls $LAYERS_DIR)
  pushd $LAYERS_DIR
  for layer in $LAYERS; do
    echo "Installing packages for: $layer. Ignore errors where there is no pip package install for the layer"
    # ref docs: https://docs.aws.amazon.com/lambda/latest/dg/python-package.html#python-package-pycache
    pip install \
    --quiet \
    --platform manylinux2014_x86_64 \
    --target=package \
    --implementation cp \
    --python-version 3.10 \
    --only-binary=:all: \
    --no-compile \
    --requirement ${layer}/requirements.txt \
    --target=${layer}/python 2>&1 | \
      grep -v "WARNING: Target directory"
    echo "Done installing dependencies for $layer"
    
  done
  popd
else
  echo "Directory $LAYERS_DIR does not exist. Skipping"
fi
# Specific to ffmpeg binary to be made avaialble in Lambda runtime
wget -P $LAYERS_DIR/ffmpeg https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
tar xvf $LAYERS_DIR/ffmpeg/ffmpeg-master-latest-linux64-gpl.tar.xz -C $LAYERS_DIR/ffmpeg
rm -rf $LAYERS_DIR/ffmpeg/ffmpeg-master-latest-linux64-gpl.tar.xz
cp $LAYERS_DIR/ffmpeg/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg $LAYERS_DIR/ffmpeg/bin
rm -rf $LAYERS_DIR/ffmpeg/ffmpeg-master-latest-linux64-gpl

[ -z "$SAMPLES_BUCKET" ] || echo "   <SAMPLES_BUCKET> with bucket name: $SAMPLES_BUCKET"
[ -z "$SAMPLES_PREFIX" ] || echo "   <SAMPLES_PREFIX> with prefix: $SAMPLES_PREFIX"
[ -z "$METADATA_PREFIX" ] || echo "   <METADATA_PREFIX> with prefix: $METADATA_PREFIX"
[ -z "$OPTIONS_PREFIX" ] || echo "   <OPTIONS_PREFIX> with prefix: $OPTIONS_PREFIX"

templates_dir=./cfn-templates
mkdir -p $templates_dir/out
[ -d "$LAYERS_DIR" ] && find $LAYERS_DIR -exec touch -d "$(date +%Y-%m-%d)T00:00:00"  '{}' \;
[ -d "$FINDER_APP_DIR" ] && find $FINDER_APP_DIR -exec touch -d "$(date +%Y-%m-%d)T00:00:00" '{}' \;
# Initialize Output
Outputs=""

pushd $templates_dir
for template in msindexer.yaml msfinder.yaml
do
  echo "Processing File " ${template}
  cat $template | 
  sed -e "s%<ARTIFACT_BUCKET_TOKEN>%$BUCKET%g" | 
  sed -e "s%<ARTIFACT_PREFIX_TOKEN>%$PREFIX"/"%g" |
  sed -e "s%<SAMPLES_BUCKET>%$SAMPLES_BUCKET%g" |
  sed -e "s%<SAMPLES_PREFIX>%$SAMPLES_PREFIX%g" |
  sed -e "s%<METADATA_PREFIX>%$METADATA_PREFIX%g" |
  sed -e "s%<OPTIONS_PREFIX>%$OPTIONS_PREFIX%g" |
  sed -e "s%<FINDER_ZIPFILE>%$finderzip%g" |
  sed -e "s%<REGION>%$region%g" >  deploy_$template

  S3PATH=s3://$BUCKET/$PREFIX/
  aws s3 cp ${tmpdir}/${finderzip} ${S3PATH}${finderzip}

  s3_template=s3://${BUCKET}/${PREFIX}/${template}
  https_template="https://${BUCKET}.s3.${region}.amazonaws.com/${PREFIX}/${template}"
  echo "S3 Template " $s3_template
  echo "HTTPS Template " $https_template
  aws cloudformation package \
  --template-file deploy_$template \
  --output-template-file ./out/${template} \
  --s3-bucket $BUCKET --s3-prefix $PREFIX \
  --region ${region} || exit 1
  echo "Uploading template file to: ${s3_template}"
  aws s3 cp ./out/${template} ${s3_template}
  echo "Validating template"
  aws cloudformation validate-template --template-url ${https_template} > /dev/null || exit 1
  templateName=${template%.*}
  templateNameUpper=`echo "$templateName" | awk '{print toupper($0)}'`
  Outputs=$Outputs${templateNameUpper}" Template URL - $https_template;"
  
  Outputs=$Outputs${templateNameUpper}" CF Launch URL - https://${region}.console.aws.amazon.com/cloudformation/home?region=${region}#/stacks/create/review?templateURL=$https_template&stackName=MediaSearch-${templateNameUpper};"
  rm -rf deploy_$template
done
popd

# Trim trailing :
Outputs="${Outputs%;}"
echo
echo
echo
echo -e "===================="
echo -e "      Outputs       "
echo -e "===================="

# Print strings   
IFS=';' read -ra STRINGS <<< "$Outputs"
for str in "${STRINGS[@]}"; do
  echo "$str" 
  echo
done

echo "Done"