import boto3
from boto3.dynamodb.conditions import Key
from itertools import groupby, chain
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

TABLE_NAME = os.environ['TABLE_NAME']
EVENT_TYPE = ['register', 'cleanup', 'asg_test_notification']
EVENT_TYPE.append('other')    # The last entry must be 'other'.

def lambda_handler(event, context):
    logger.debug(f'Event: {event}')
    event_type, message = parse_event(event)
    return dispatch(event_type, message)

def dispatch(event_type, message):
    assert event_type in EVENT_TYPE
    fun = getattr(sys.modules[__name__], event_type)
    return fun(message)

def other(message):
    logger.error('Invalid event')

def register(message):
    logger.info(f'Register: {message["instance"]}, {message["name"]}')
    table = boto3.resource('dynamodb').Table(TABLE_NAME)
    table.put_item(Item=message)

def asg_test_notification(message):
    logger.info('Ignored: ASG test notification')

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

# For testing; require existing DynamoDB items.
#cleanup = lambda msg: cleanup(msg, ec2instanceid='test', del_test_item=False)

def parse_event(event):
    '''Determine event type, and get the payload.

    :returns: (event_type, message) where event_type is in EVENT_TYPE
    :rtype: (str, dict)
    '''
    event_type, message = EVENT_TYPE[-1], {}

    # Parse payload
    try:
        sns_payload = event['Records'][0]['Sns']
        message = json.loads(sns_payload['Message'])
        attrib = sns_payload['MessageAttributes']
    except:
        return event_type, {}

    # Determine event type: cycle all EVENT_TYPE but 'other'.
    for evtype in EVENT_TYPE[:-1]:
        is_evtype = getattr(sys.modules[__name__], f'is_{evtype}_event')
        if is_evtype(message, attrib):
            event_type = evtype
            break

    return event_type, message

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
def is_asg_test_notification_event(message, attrib):
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
