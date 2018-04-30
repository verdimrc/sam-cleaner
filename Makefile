# Usage: make <target> REGION=ap-southeast-1 EC2_KEY_NAME=test-keypair AZ=a

# Change me
SAM_S3_BUCKET=vm-lambdas-$(REGION)
SAM_S3_PREFIX=sam-cleaner
KEEP_LOG_GROUPS=false
SAM_STACK_NAME=cleaner-sam
USER_STACK_NAME=cleaner-user
USER_S3_BUCKET=test-sam-cleaner
AZ_NAME=$(REGION)$(AZ)

# NOTE: SAM or sample user-stack templates may have additional optional
# parameters. You should change them as necessary to suit your need, e.g.,
# whether you need higher DynamoDB read/write capacity units, etc.

.PHONY : create_bucket package deploy sam-output create-stack update-stack clean-s3

sam: package deploy
all: package deploy sam-output create-stack

create_bucket :
	-aws --region $(REGION) s3api create-bucket \
		--bucket $(SAM_S3_BUCKET) \
		--create-bucket-configuration LocationConstraint=$(REGION)

package : create_bucket
	aws --region $(REGION) cloudformation package \
		--template-file ./template.yaml \
		--s3-bucket $(SAM_S3_BUCKET) \
		--s3-prefix $(SAM_S3_PREFIX) \
		--output-template-file packaged-template.yaml

deploy :
	aws --region $(REGION) cloudformation deploy \
		--template-file ./packaged-template.yaml \
		--stack-name $(SAM_STACK_NAME) \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides "KeepLogGroups=$(KEEP_LOG_GROUPS)"

clean-s3 :
	aws s3 ls s3://$(SAM_S3_BUCKET)/$(SAM_S3_PREFIX)/ \
		| awk '{print $4}' \
		| xargs -n1 -I{} aws s3 rm s3://$(SAM_S3_BUCKET)/$(SAM_S3_PREFIX)/{}

sam-output :
	@echo This make target needs the galen-resolve command
	$(eval SAM_OUTPUT:=$(shell galen-resolve -f csv --region $(REGION) \
							-o $(SAM_STACK_NAME)/CleanerFunRoleName \
							-o $(SAM_STACK_NAME)/CleanerFunARN))
	$(eval CLEANER_LAMBDA_ARN:=$(shell echo "$(SAM_OUTPUT)" | tr ' ' '\n' | grep CleanerFunARN | cut -d, -f3))
	$(eval CLEANER_ROLE:=$(shell echo "$(SAM_OUTPUT)" | tr ' ' '\n' | grep CleanerFunRoleName | cut -d, -f3))
	@echo CLEANER_LAMBDA_ARN=$(CLEANER_LAMBDA_ARN)
	@echo CLEANER_ROLE=$(CLEANER_ROLE)

create-stack : sam-output
	aws --region $(REGION) cloudformation create-stack --disable-rollback \
		--stack-name $(USER_STACK_NAME) \
		--capabilities CAPABILITY_NAMED_IAM \
		--template-body file://user-stack.yaml \
		--parameters \
			"ParameterKey=CleanerLambdaARN,ParameterValue=$(CLEANER_LAMBDA_ARN)" \
			"ParameterKey=CleanerRole,ParameterValue=$(CLEANER_ROLE)" \
			"ParameterKey=S3Bucket,ParameterValue=$(USER_S3_BUCKET)" \
			"ParameterKey=Ec2KeyName,ParameterValue=$(EC2_KEY_NAME)" \
			"ParameterKey=AZ,ParameterValue=$(AZ_NAME)"

update-stack : sam-output
	aws --region $(REGION) cloudformation update-stack \
		--stack-name $(USER_STACK_NAME) \
		--capabilities CAPABILITY_NAMED_IAM \
		--template-body file://user-stack.yaml \
		--parameters \
			"ParameterKey=CleanerLambdaARN,ParameterValue=$(CLEANER_LAMBDA_ARN)" \
			"ParameterKey=CleanerRole,ParameterValue=$(CLEANER_ROLE)" \
			"ParameterKey=S3Bucket,ParameterValue=$(USER_S3_BUCKET)" \
			"ParameterKey=Ec2KeyName,ParameterValue=$(EC2_KEY_NAME)" \
			"ParameterKey=AZ,ParameterValue=$(AZ_NAME)"
