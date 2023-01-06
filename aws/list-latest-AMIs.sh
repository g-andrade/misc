#!/usr/bin/env bash

set -eu

function error {
    MSG=$1
    echo "$MSG" >&2
    exit 1
}

function check_cmd {
    CMD=$1
    which "$CMD" >/dev/null || error "$CMD is missing"
}

check_cmd aws
check_cmd yq

AWS_PROFILE=${AWS_PROFILE:-default}
EC2_VOLUME_TYPE=${EC2_VOLUME_TYPE:-ebs}
AMI_NAME=${AMI_NAME:-amzn2-ami-hvm-x86_64-$EC2_VOLUME_TYPE}

>&2 echo "Listing AWS regions..."
AWS_REGIONS=$(
    aws ec2 --profile="${AWS_PROFILE}" --region=eu-west-1 describe-regions \
    | yq -r '.Regions[].RegionName' \
    | sort
)

for AWS_REGION in $AWS_REGIONS; do
    >&2 echo "Retrieving AMI for ${AWS_REGION}..."
    aws ssm get-parameters \
        --profile="${AWS_PROFILE}" --region="${AWS_REGION}" \
        --names "/aws/service/ami-amazon-linux-latest/${AMI_NAME}" \
        --query 'Parameters[0]' \
        --output json \
    | yq '(.. | select(tag == "!!int")) tag= "!!str"' \
    | yq '.Value lineComment=("version " + .Version + ", " + .LastModifiedDate)' \
    | yq "{\"${AWS_REGION}\": {\"${EC2_VOLUME_TYPE}\": .Value}}" \
    | yq '... style=""'
done \
| yq '.'
