# Overview

An EC2 instance can create additional AWS resources. When this instance
terminates, you must clean those resources to prevent them from lingering
forever in your AWS account.

This SAM automates the cleaning-up of AWS resources created by EC2 instances
that are members of an ASG.

Available on the [AWS Serverless Application Repository](https://aws.amazon.com/serverless)


# Limitations

This SAM deletes resources that `boto3` can delete with just a single call.

This SAM does not support resources that `boto3` cannot delete recursively with
just a single call. Examples are S3 buckets or S3 directories.

This SAM can only delete non-VPC resources.

Do note also the limitations on the maximum concurrent invocations of Lambda
functions, and DynamoDB read/write capacity units.


# SAM Architecture

Internally, this SAM interacts with outside world via an SNS topic. EC2
instances registers resources by posting messages to the SNS topic. Likewise,
ASG sends `autoscaling:EC2_INSTANCE_TERMINATE` to the same SNS topic.

The SAM stores registered resources in a DynamoDB table, with each resource
occupies one DynamoDB item.

Also note that the SAM itself, when deployed, has only very locked-down
permissions: only basic Lambda permissions and CRUD to its own DynamoDB table.

For the SAM to be able to delete your resources, you need to fetch its stack's
output `CleanerFunRoleName` and attach additional policies to grant this SAM
abilities to delete additional resources. See the `user-stack.yaml` example.

**Fun fact**: the SAM template (`template.yaml`) exports hidden role
`CleanerFunRole` as output `CleanerFunRoleName`.


# Usage

Once you package and deploy the SAM, here's what you need to do next:

1. connect your ASG to the SAM
2. EC2 instances (who're member of the ASG) to register their resources to SAM.

When you terminate those instances, or even the whole ASG, the SAM should clean
resources you've registered.


## Connect ASG to SAM

Follow these steps to connect your ASG to SAM:

1. Create an SNS topic
2. Subscribe the SAM to the SNS topic
3. Attach additional policies to the SAM to allow the SAM to delete resources
   of your choice.
4. Configure your ASG to publish `autoscaling:EC2_INSTANCE_TERMINATE` events to
   the SNS topic. Note that when using CloudFormation, you must explicitly set
   your ASG to depends on your custom SAM policies and your SNS topic. This
   ensures when deleting your stack, the SAM can properly receive termination
   events and still have the apropriate policies to delete your resources.
5. Your EC2 instance must tell the SAM the resources to clean-up when the
   instance terminates. To register a resource, the instance simply posts to
   the SNS topic.


## Register Resources to SAM

Below is a sample Bash script that an EC2 instance can use to register a
CloudWatch alarm to SAM:

```bash
#!/bin/bash

region=ap-southeast-1
sns_topic=arn:aws:sns:ap-southeast-1:xxxxyyyyzzzz:cleaner-user2-NotificationTopic-1NH44J4PS48WO
instance=i-0865a14588afdf120

mattr='{"sam-cleaner":{"DataType":"String","StringValue":"register"}}'
message=$(cat << EOF
{
  "instance": "${instance}",
  "name": "alarm-belongs-to-${instance}",
  "properties": {
    "service": "cloudwatch",
    "resource": "alarms",
    "kwargs": {"AlarmNames": ["alarm-belongs-to-${instance}"]}
  }
}
EOF
)

aws --region $region sns publish --topic-arn $sns_topic --message "${message}" --message-attributes "${mattr}"
```

After the alarm is registered, when the EC2 instance (who's a member of an ASG)
terminates, the SAM will be able to delete the alarm. The `name` is a
human-readable string used internally by SAM (specifically, as sort keys for
a DynamoDB table). The `properties` instructs SAM to invoke these `boto3`
calls:

```python
# Note: X refers to key x in message's properties.

client = boto3.service(SERVICE)
client.delete_RESOURCE(**KWARGS)
```

Thus, the above sample message translates to:

```python
client = boto3.service('cloudwatch')
client.delete_alarms(AlarmNames=['alarm-belongs-to-i-02269cdabe31dd5d6'])
```


# Demonstration with Sample CloudFormation Template

Follow these steps:

1. Deploy this SAM, either manually or via SAR.
2. Deploy CloudFormation template `user-stack.yaml`. This user stack creates
   an ASG with one EC2 instance.
3. Observe the alarm and S3 object created by the EC2 instance.
4. Either terminate the EC2 instance, or completely delete the stack.
5. Observe the alarm and S3 object created by the deleted EC2 instance are gone.

Note that the templates have more parameters to customize beyond what's shown
here. Please look at those templates to learn about available parameters.


## Manually Package and Deploy SAM

This section applies only if you don't deploy from SAR.

```bash
# Set variables (change as necessary)
$ REGION=ap-southeast-1
$ SAM_S3_BUCKET=my-sam-$REGION
$ SAM_S3_PREFIX=sam-cleaner
$ SAM_STACK_NAME=cleaner-sam

# Create S3 bucket for the SAM
$ aws --region ap-southeast-1 s3api create-bucket \
        --bucket $SAM_S3_BUCKET \
        --create-bucket-configuration LocationConstraint=$REGION
...

# Package SAM
$ aws --region $REGION cloudformation package \
        --template-file ./template.yaml \
        --s3-bucket $SAM_S3_BUCKET \
        --s3-prefix $SAM_S3_PREFIX \
        --output-template-file packaged-template.yaml
Uploading to sam-cleaner/4e457e907e08b0150588a3ef75069b46  9543 / 9543.0  (100.00%)
Successfully packaged artifacts and wrote output template to file packaged-template.yaml.
Execute the following command to deploy the packaged template
aws cloudformation deploy --template-file /Users/marcverd/src/sam-cleaner/packaged-template.yaml --stack-name <YOUR STACK NAME>

$ aws --region $REGION cloudformation deploy \
        --template-file ./packaged-template.yaml \
        --stack-name $SAM_STACK_NAME \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides "KeepLogGroups=false"

Waiting for changeset to be created..
Waiting for stack create/update to complete
Successfully created/updated stack - cleaner-sam
```

Once deployed, the SAM stack will export two values: the lambda ARN and the
lambda role. You'll need fetch these two values for the next step.


## Deploy Sample User-Stack

You'll need the lambda's ARN and role name which are found in the SAM stack's
outputs.

```bash
# These two values are obtained from SAM stack's outputs (change as necessary).
$ CLEANER_LAMBDA_ARN=arn:aws:lambda:ap-southeast-1:xxxxyyyyzzzz:function:cleaner-sam-CleanerFun-151HR9YZFBLEV
$ CLEANER_ROLE=cleaner-sam-CleanerRole-13L5CZAN5DHAE

# Additional variables (change as necessary)
$ REGION=ap-southeast-1
$ STACK_NAME=cleaner-user
$ KEYPAIR=my-keypair
$ AZ=${REGION}a

$ USER_S3_BUCKET=my-bucket

$ aws --region $REGION cloudformation create-stack --disable-rollback \
        --stack-name $STACK_NAME \
        --capabilities CAPABILITY_NAMED_IAM \
        --template-body file://user-stack.yaml \
        --parameters \
            "ParameterKey=CleanerLambdaARN,ParameterValue=$CLEANER_LAMBDA_ARN" \
            "ParameterKey=CleanerRole,ParameterValue=$CLEANER_ROLE" \
            "ParameterKey=Ec2KeyName,ParameterValue=$KEYPAIR" \
            "ParameterKey=AZ,ParameterValue=$AZ" \
            "ParameterKey=S3Bucket,ParameterValue=$USER_S3_BUCKET"
{
    "StackId": "arn:aws:cloudformation:ap-southeast-1:xxxxyyyyzzzz:stack/cleaner-user/55d294e0-3e2b-11e8-b40f-500c336f38f2"
}
```

**NOTE**: please pay attention on `DependsOn` of the ASG which instruct
CloudFormation to delete the ASG first, and only then unsubscribe the SAM from
the SNS topic and revoke the SAM's access to this stack's resources. Otherwise,
you may still get orphan resources when deleting the whole stack.


## Test Functionalities: Delete EC2 Instance or User Stack

Go to your AWS Console, and you should observe one alarm and one S3 object in
the S3 bucket created by the sample user-stack --- these resources embed the
instance id in their name to let you easily spot them.

Now, go to EC2 and terminates the instance. Wait for a while and you should
notice the instance's alarm and S3 object are gone.

Because the terminated instance was member of an ASG, the ASG will launch a new
instance. As with the previous instance, you'll notice this instance also create
an alarm and an S3 object.

Now, terminate the whole CloudFormation stack. Once the stack is completely
wiped out, you should notice the alarm and S3 object of the last EC2 instance
are also deleted by the SAM.
