import boto3
from boto3.dynamodb.conditions import Key
from itertools import groupby, chain
import json
import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

TABLE_NAME = os.environ['TABLE_NAME']

def lambda_handler(event, context):
    logger.debug(f'Event: {event}')

    event_type, message = parse_event(event)
    if event_type == 'REGISTER':
        return register(message) 
    elif event_type == 'CLEANUP':
        #return cleanup(message, ec2instanceid='test', del_test_item=False)
        return cleanup(message)
    elif event_type == 'ASG_TEST_NOTIFICATION':
        logger.info('Ignored: ASG test notification')
    else:
        raise ValueError('Invalid event')

def parse_event(event):
    OTHER = 'OTHER'
    message = {}
    
    # Parse payload
    try:
        sns_payload = event['Records'][0]['Sns']
        message = json.loads(sns_payload['Message'])
        attrib = sns_payload['MessageAttributes']
    except:
        return OTHER, {}

    # Determine event type
    if is_register_event(message, attrib):
        event_type = 'REGISTER'
    elif is_cleanup_event(message, attrib):
        event_type = 'CLEANUP'
    elif is_asg_test_notification(message, attrib):
        event_type = 'ASG_TEST_NOTIFICATION'
    else:
        event_type = OTHER

    return event_type, message

def register(message):
    table = boto3.resource('dynamodb').Table(TABLE_NAME)
    table.put_item(Item=message)

def cleanup(message, ec2instanceid: str=''):
    '''
    :param ec2instanceid: Non-empty string to fix instance id to a specific
        value. Useful for testing (e.g., manually invoke lambda from the
        web console)
    '''

    # Parse SNS message for required data
    if ec2instanceid == '':
        ec2instanceid = message['EC2InstanceId']

    logger.info(f'Clean-up resources of instance {ec2instanceid}')

    # Pull instance resources from DynamoDB
    table = boto3.resource('dynamodb').Table(TABLE_NAME)
    response = table.query(
                    KeyConditionExpression=Key('instance').eq(ec2instanceid))

    # Clean-up resources
    get_service = lambda x: x['properties']['service']
    sorted_items = sorted(response['Items'], key=get_service)
    for service, resources in groupby(sorted_items, key=get_service):
        del_group(table, ec2instanceid, service, resources)

    logger.info(f'Clean-up done for instance {ec2instanceid}')

def falsify_exception(f):
    '''
    :returns: False on exception, else the return value of f
    '''
    def f_wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except:
            return False
    return f_wrapper

@falsify_exception
def is_register_event(message, attrib):
    return attrib['sam-cleaner']['Value'] == 'register'

@falsify_exception
def is_cleanup_event(message, attrib):
    return ('AutoScalingGroupARN' in message
            and message['Event'] == 'autoscaling:EC2_INSTANCE_TERMINATE')

@falsify_exception
def is_asg_test_notification(message, attrib):
    return ('AutoScalingGroupARN' in message
            and message['Event'] == 'autoscaling:TEST_NOTIFICATION')

def del_group(table, instance, service, resources):
    '''Delete resources of the same service and their corresponding entry in
    DynamoDB.
    
    Re-use the same boto3 client to delete all the resources. And regardless
    of deletion outcome, proceed to remove the corresponding DynamoDB item.
    '''
    client = boto3.client(service)
    del_resource, resources2 = get_del_function(resources, client)
    for r in resources2:
        try:
            del_resource(**r['properties']['kwargs'])
        except:
            from traceback import format_exc
            logger.error(f'Resource {r["name"]}:\n{format_exc()}')
            pass
        finally:
            table.delete_item(Key=dict(instance=instance, name=r['name']))

def get_del_function(resources, client):
    '''This function peeks into the resources iterator, hence must return
    another iterator starting at the original head.

    :returns: a tuple of (del_function, group_iterator)
    '''
    head = next(resources)
    return (getattr(client, f"delete_{head['properties']['resource']}"),
            chain([head], resources))
