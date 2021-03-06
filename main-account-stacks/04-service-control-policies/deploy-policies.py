import boto3
import cfnresponse
import time
import random
import re

o = boto3.client("organizations")

CREATE = 'Create'
UPDATE = 'Update'
DELETE = 'Delete'
SCP = "SERVICE_CONTROL_POLICY"


def root():
    return o.list_roots()['Roots'][0]


def root_id():
    return root()['Id']


def scp_enabled():
    enabled_policies = root()['PolicyTypes']
    return {"Type": SCP, "Status": "ENABLED"} in enabled_policies


def exception_handling(function):
    def catch(event, context):
        try:
            function(event, context)
        except Exception as e:
            print(e)
            print(event)
            cfnresponse.send(event, context, cfnresponse.FAILED, {})

    return catch


@exception_handling
def enable_service_control_policies(event, context):
    RequestType = event["RequestType"]
    if RequestType == CREATE and not scp_enabled():
        r_id = root_id()
        print('Enable SCP for root: {}'.format(r_id))
        o.enable_policy_type(RootId=r_id, PolicyType=SCP)
    cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, 'SCP')


def with_retry(function, **kwargs):
    for i in [0, 3, 9, 15, 30]:
        # Random sleep to not run into concurrency problems when adding or attaching multiple SCPs
        # They have to be added/updated/deleted one after the other
        sleeptime = i + random.randint(0, 5)
        print('Running {} with Sleep of {}'.format(function.__name__, sleeptime))
        time.sleep(sleeptime)
        try:
            response = function(**kwargs)
            print("Response for {}: {}".format(function.__name__, response))
            return response
        except o.exceptions.ConcurrentModificationException as e:
            print('Exception: {}'.format(e))
    raise Exception


@exception_handling
def handler(event, context):
    RequestType = event["RequestType"]
    Properties = event["ResourceProperties"]
    LogicalResourceId = event["LogicalResourceId"]
    PhysicalResourceId = event.get("PhysicalResourceId")
    Policy = Properties["Policy"]
    Attach = Properties["Attach"] == 'true'

    print('RequestType: {}'.format(RequestType))
    print('PhysicalResourceId: {}'.format(PhysicalResourceId))
    print('LogicalResourceId: {}'.format(LogicalResourceId))
    print('Attach: {}'.format(Attach))

    parameters = dict(
        Content=Policy,
        Description="Baseline Policy - {}".format(LogicalResourceId),
        Name=LogicalResourceId,
    )

    policy_id = PhysicalResourceId
    paginator = o.get_paginator('list_policies')
    policies = [policy['Id'] for page in paginator.paginate(Filter=SCP) for policy in
                page['Policies']
                if policy['Name'] == LogicalResourceId]
    if policies:
        policy_id = policies[0]
    if RequestType == CREATE:
        print('Creating Policy: {}'.format(LogicalResourceId))
        response = with_retry(o.create_policy,
                              **parameters, Type=SCP
                              )
        policy_id = response["Policy"]["PolicySummary"]["Id"]
        if Attach:
            with_retry(o.attach_policy, PolicyId=policy_id, TargetId=root_id())
    elif RequestType == UPDATE:
        print('Updating Policy: {}'.format(LogicalResourceId))
        with_retry(o.update_policy, PolicyId=policy_id, **parameters)
    elif RequestType == DELETE:
        print('Deleting Policy: {}'.format(LogicalResourceId))
        # Same as above
        if re.match('p-[0-9a-z]+', policy_id):
            if policy_attached(policy_id):
                with_retry(o.detach_policy, PolicyId=policy_id, TargetId=root_id())
            with_retry(o.delete_policy, PolicyId=policy_id)
        else:
            print('{} is no valid PolicyId'.format(policy_id))
    else:
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, policy_id)
    cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, policy_id)


def policy_attached(policy_id):
    return [p['Id'] for p in
            o.list_policies_for_target(TargetId=root_id(), Filter='SERVICE_CONTROL_POLICY')['Policies'] if
            p['Id'] == policy_id]
