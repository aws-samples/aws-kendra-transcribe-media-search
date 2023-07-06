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

# Add trailing slash to prefix if needed
[[ "${PREFIX}" != */ ]] && PREFIX="${PREFIX}/"


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

# Install pytube
[ -d lambdalayer ] && rm -fr lambdalayer
mkdir -p lambdalayer/pytube/python
pip3 install pytube -t lambdalayer/pytube/python
if [[ `pip3 list --path lambdalayer/pytube/python | grep pytube | awk '{print $2}'` = '15.0.0' ]]; then
  echo "Temp fix specific to pytube 15.0.0 cipher.py"
  replacewith=`head -287 lambdalayer/pytube/python/pytube/cipher.py| tail -1 | sed 's/;//g'`
  lines=`wc -l lambdalayer/pytube/python/pytube/cipher.py | awk '{print $1}'`
  head -286 lambdalayer/pytube/python/pytube/cipher.py > cipher.py && echo $replacewith >> cipher.py && tail -`expr $lines - 287` lambdalayer/pytube/python/pytube/cipher.py >> cipher.py
  mv cipher.py lambdalayer/pytube/python/pytube/cipher.py
fi
echo "Create timestamped zipfile for lambdas and layers"
# pytube-llayer
pytubellayerzip=pytubellayer_$timestamp.zip
pushd lambdalayer/pytube
zip -r $tmpdir/$pytubellayerzip .
popd

# ytindexer
ytindexerzip=ytindexer_$timestamp.zip
pushd lambda/ytindexer
zip -r $tmpdir/$ytindexerzip *.py
popd
# indexer
indexerzip=indexer_$timestamp.zip
pushd lambda/indexer
zip -r $tmpdir/$indexerzip *.py
popd
# build-trigger
buildtriggerzip=buildtrigger_$timestamp.zip
pushd lambda/build-trigger
zip -r $tmpdir/$buildtriggerzip *.py
popd
# token-enabler
tokenenablerzip=tokenenabler_$timestamp.zip
pushd lambda/token-enabler
zip -r $tmpdir/$tokenenablerzip *.py
popd

echo "Create zipfile for AWS Amplify/CodeCommit"
finderzip=finder_$timestamp.zip
zip -r $tmpdir/$finderzip ./* -x "node_modules*"


echo "Inline edit Cfn templates to replace "
echo "   <ARTIFACT_BUCKET_TOKEN> with bucket name: $BUCKET"
echo "   <ARTIFACT_PREFIX_TOKEN> with prefix: $PREFIX"
echo "   <PYTUBELLAYER_ZIPFILE> with zipfile: $pytubellayerzip"
echo "   <YTINDEXER_ZIPFILE> with zipfile: $ytindexerzip"
echo "   <INDEXER_ZIPFILE> with zipfile: $indexerzip"
echo "   <BUILDTRIGGER_ZIPFILE> with zipfile: $buildtriggerzip"
echo "   <FINDER_ZIPFILE> with zipfile: $finderzip"
echo "   <TOKEN_ENABLER_ZIPFILE> with zipfile: $tokenenablerzip"
echo "   <REGION> with region: $region"
[ -z "$SAMPLES_BUCKET" ] || echo "   <SAMPLES_BUCKET> with bucket name: $SAMPLES_BUCKET"
[ -z "$SAMPLES_PREFIX" ] || echo "   <SAMPLES_PREFIX> with prefix: $SAMPLES_PREFIX"
[ -z "$METADATA_PREFIX" ] || echo "   <METADATA_PREFIX> with prefix: $METADATA_PREFIX"
[ -z "$OPTIONS_PREFIX" ] || echo "   <OPTIONS_PREFIX> with prefix: $OPTIONS_PREFIX"
for template in msindexer.yaml msfinder.yaml
do
   echo preprocessing $template
   cat cfn-templates/$template | 
    sed -e "s%<ARTIFACT_BUCKET_TOKEN>%$BUCKET%g" | 
    sed -e "s%<ARTIFACT_PREFIX_TOKEN>%$PREFIX%g" |
    sed -e "s%<PYTUBELLAYER_ZIPFILE>%$pytubellayerzip%g" |
    sed -e "s%<YTINDEXER_ZIPFILE>%$ytindexerzip%g" |
    sed -e "s%<INDEXER_ZIPFILE>%$indexerzip%g" |
    sed -e "s%<BUILDTRIGGER_ZIPFILE>%$buildtriggerzip%g" |
    sed -e "s%<FINDER_ZIPFILE>%$finderzip%g" |
    sed -e "s%<TOKEN_ENABLER_ZIPFILE>%$tokenenablerzip%g" |
    sed -e "s%<SAMPLES_BUCKET>%$SAMPLES_BUCKET%g" |
    sed -e "s%<SAMPLES_PREFIX>%$SAMPLES_PREFIX%g" |
    sed -e "s%<METADATA_PREFIX>%$METADATA_PREFIX%g" |
    sed -e "s%<OPTIONS_PREFIX>%$OPTIONS_PREFIX%g" |
    sed -e "s%<REGION>%$region%g" > $tmpdir/$template
done

S3PATH=s3://$BUCKET/$PREFIX
echo "Copy $tmpdir/* to $S3PATH/"
for f in msfinder.yaml msindexer.yaml $pytubellayerzip $ytindexerzip $indexerzip $buildtriggerzip $finderzip $tokenenablerzip
do
  aws s3 cp ${tmpdir}/${f} ${S3PATH}${f} || exit 1
done

if $PUBLIC; then
  echo "Setting public read ACLs on published artifacts"
  for f in msfinder.yaml msindexer.yaml $pytubellayerzip $ytindexerzip $indexerzip $buildtriggerzip $finderzip $tokenenablerzip
  do
    echo s3://${BUCKET}/${PREFIX}${f}
    aws s3api put-object-acl --acl public-read --bucket ${BUCKET} --key ${PREFIX}${f}
  done
fi

# get default media bucket region and warn if it is different than Cfn bucket region
# media bucket must be in the same region as deployed stack (or Transcribe jobs fail)
if [ ! -z "$SAMPLES_BUCKET" ]; then
    dflt_media_region=$(aws s3api get-bucket-location --bucket $SAMPLES_BUCKET --query "LocationConstraint" --output text) || dflt_media_region="us-east-1"
    [ -z "dflt_media_region" -o "dflt_media_region" == "None" ] && dflt_media_region=us-east-1;
    if [ "$dflt_media_region" != "$region" ]; then
        echo "WARNING!!! Default media bucket region ($dflt_media_region) does not match deployment bucket region ($region).. Media bucket ($SAMPLES_BUCKET) must be in same region as deployment bucket ($BUCKET)"
    fi
fi

echo "Outputs"
indexer_template="https://s3.${region}.amazonaws.com/${BUCKET}/${PREFIX}msindexer.yaml"
finder_template="https://s3.${region}.amazonaws.com/${BUCKET}/${PREFIX}msfinder.yaml"
echo Indexer Template URL: $indexer_template
echo Finder Template URL: $finder_template
echo Indexer - CF Launch URL: https://${region}.console.aws.amazon.com/cloudformation/home?region=${region}#/stacks/create/review?templateURL=${indexer_template}\&stackName=MediaSearch-Indexer
echo Finder - CF Launch URL: https://${region}.console.aws.amazon.com/cloudformation/home?region=${region}#/stacks/create/review?templateURL=${finder_template}\&stackName=MediaSearch-Finder

echo Done
exit 0

