#!/bin/bash

###############################################################################
# Create new Cfn artifacts bucket if not already existing
# Modify templates to reference new bucket names and prefixes
# create lambda zipfiles with timestamps to ensure redeployment on stack update
# Upload templates to S3 bucket
###############################################################################

USAGE="$0 cfn_bucket cfn_prefix [dflt_media_bucket] [dflt_media_prefix]"

BUCKET=$1
[ -z "$BUCKET" ] && echo "Cfn bucket name is required parameter. Usage $USAGE" && exit 1

PREFIX=$2
[ -z "$PREFIX" ] && echo "Prefix is required parameter. Usage $USAGE" && exit 1

SAMPLES_BUCKET=$3
SAMPLES_PREFIX=$4

# Add trailing slash to prefix if needed
[[ "${PREFIX}" != */ ]] && PREFIX="${PREFIX}/"


# Create bucket if it doesn't already exist
aws s3api list-buckets --query 'Buckets[].Name' | grep "\"$BUCKET\"" > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "Creating s3 bucket: $BUCKET"
  aws s3api create-bucket --bucket ${BUCKET} || exit 1
  aws s3api put-bucket-versioning --bucket ${BUCKET} --versioning-configuration Status=Enabled || exit 1
else
  echo "Using existing bucket: $BUCKET"
fi

echo -n "Make temp dir: "
timestamp=$(date "+%Y%m%d_%H%M")
tmpdir=/tmp/mediasearch
[ -d /tmp/mediasearch ] && rm -fr /tmp/mediasearch
mkdir -p $tmpdir
pwd

echo "Create timestamped zipfile for lambdas"
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

echo "Create zipfile for AWS Amplify/CodeCommit"
finderzip=finder_$timestamp.zip
zip -r $tmpdir/$finderzip ./*

echo "Inline edit Cfn templates to replace "
echo "   <ARTIFACT_BUCKET_TOKEN> with bucket name: $BUCKET"
echo "   <ARTIFACT_PREFIX_TOKEN> with prefix: $PREFIX"
echo "   <INDEXER_ZIPFILE> with zipfile: $indexerzip"
echo "   <BUILDTRIGGER_ZIPFILE> with zipfile: $buildtriggerzip"
echo "   <FINDER_ZIPFILE> with zipfile: $finderzip"
[ -z "$SAMPLES_BUCKET" ] || echo "   <SAMPLES_BUCKET> with bucket name: $SAMPLES_BUCKET"
[ -z "$SAMPLES_PREFIX" ] || echo "   <SAMPLES_PREFIX> with prefix: $SAMPLES_PREFIX"
for template in msindexer.yaml msfinder.yaml
do
   echo preprocessing $template
   cat cfn-templates/$template | 
    sed -e "s%<ARTIFACT_BUCKET_TOKEN>%$BUCKET%g" | 
    sed -e "s%<ARTIFACT_PREFIX_TOKEN>%$PREFIX%g" |
    sed -e "s%<INDEXER_ZIPFILE>%$indexerzip%g" |
    sed -e "s%<BUILDTRIGGER_ZIPFILE>%$buildtriggerzip%g" |
    sed -e "s%<FINDER_ZIPFILE>%$finderzip%g" |
    sed -e "s%<SAMPLES_BUCKET>%$SAMPLES_BUCKET%g" |
    sed -e "s%<SAMPLES_PREFIX>%$SAMPLES_PREFIX%g" > $tmpdir/$template
done

S3PATH=s3://$BUCKET/$PREFIX
echo "Copy $tmpdir/* to $S3PATH/"
aws s3 rm ${S3PATH} --recursive
for f in msfinder.yaml msindexer.yaml $indexerzip $buildtriggerzip $finderzip
do
aws s3 cp ${tmpdir}/${f} ${S3PATH}${f} || exit 1
done

# get bucket region for owned accounts, and generate URLs for Cfn templates
region=$(aws s3api get-bucket-location --bucket $BUCKET --query "LocationConstraint" --output text) || region="us-east-1"
[ -z "$region" -o "$region" == "None" ] && region=us-east-1;
echo "Outputs"
echo Indexer CF Template URL: https://s3.${region}.amazonaws.com/${BUCKET}/${PREFIX}msindexer.yaml
echo Finder CF Template URL: https://s3.${region}.amazonaws.com/${BUCKET}/${PREFIX}msfinder.yaml


echo Done
exit 0