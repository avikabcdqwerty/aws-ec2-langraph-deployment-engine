import os
import sys
import time
import logging

import boto3  # AWS SDK for Python
from botocore.exceptions import ClientError

# --- Configuration ---
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
EC2_INSTANCE_TYPE = "t3.medium"
EC2_AMI_ID = os.getenv("EC2_AMI_ID", "ami-0c94855ba95c71c99")  # Ubuntu 18.04 LTS (example)
EC2_KEY_NAME = os.getenv("EC2_KEY_NAME", "my-ec2-keypair")
EC2_SECURITY_GROUP_NAME = "langraph-web-sg"
EC2_TAG_NAME = "LangraphWebServer"
EC2_USER_DATA_SCRIPT = """#!/bin/bash
sudo apt-get update -y
sudo apt-get install -y python3-pip nginx
pip3 install langraph flask
cat <<EOF > /home/ubuntu/app.py
from flask import Flask, request, jsonify
import langraph

app = Flask(__name__)

@app.route('/')
def home():
    return "Welcome to Langraph-powered Website!"

@app.route('/ai', methods=['POST'])
def ai_feature():
    data = request.json
    # Example: Use Langraph to process input
    result = langraph.process(data.get('text', ''))
    return jsonify({'result': result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
EOF
nohup python3 /home/ubuntu/app.py &
sudo rm /etc/nginx/sites-enabled/default
echo 'server { listen 80; location / { proxy_pass http://localhost:5000; } }' | sudo tee /etc/nginx/sites-available/langraph
sudo ln -s /etc/nginx/sites-available/langraph /etc/nginx/sites-enabled/
sudo systemctl restart nginx
"""

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def create_security_group(ec2, vpc_id):
    try:
        response = ec2.create_security_group(
            GroupName=EC2_SECURITY_GROUP_NAME,
            Description='Security group for Langraph web server',
            VpcId=vpc_id
        )
        sg_id = response['GroupId']
        logging.info(f"Created security group {EC2_SECURITY_GROUP_NAME} with ID {sg_id}")

        # Allow SSH, HTTP
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 22,
                    'ToPort': 22,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                },
                {
                    'IpProtocol': 'tcp',
                    'FromPort': 80,
                    'ToPort': 80,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                }
            ]
        )
        logging.info("Configured security group ingress rules.")
        return sg_id
    except ClientError as e:
        if "InvalidGroup.Duplicate" in str(e):
            # Get existing SG ID
            sgs = ec2.describe_security_groups(GroupNames=[EC2_SECURITY_GROUP_NAME])
            return sgs['SecurityGroups'][0]['GroupId']
        else:
            logging.error(f"Error creating security group: {e}")
            sys.exit(1)

def launch_ec2_instance():
    ec2_resource = boto3.resource('ec2', region_name=AWS_REGION)
    ec2_client = boto3.client('ec2', region_name=AWS_REGION)

    # Get default VPC
    vpcs = ec2_client.describe_vpcs()
    vpc_id = vpcs['Vpcs'][0]['VpcId']

    # Create or get security group
    sg_id = create_security_group(ec2_client, vpc_id)

    # Launch EC2 instance
    try:
        instances = ec2_resource.create_instances(
            ImageId=EC2_AMI_ID,
            InstanceType=EC2_INSTANCE_TYPE,
            KeyName=EC2_KEY_NAME,
            MinCount=1,
            MaxCount=1,
            SecurityGroupIds=[sg_id],
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [{'Key': 'Name', 'Value': EC2_TAG_NAME}]
            }],
            UserData=EC2_USER_DATA_SCRIPT
        )
        instance = instances[0]
        logging.info(f"Launching EC2 instance {instance.id}...")
        instance.wait_until_running()
        instance.reload()
        public_ip = instance.public_ip_address
        logging.info(f"EC2 instance is running at {public_ip}")
        return instance
    except ClientError as e:
        logging.error(f"Error launching EC2 instance: {e}")
        sys.exit(1)

def setup_monitoring(instance_id):
    # Enable basic monitoring (CloudWatch)
    ec2_client = boto3.client('ec2', region_name=AWS_REGION)
    try:
        ec2_client.monitor_instances(InstanceIds=[instance_id])
        logging.info(f"Enabled detailed monitoring for instance {instance_id}")
    except ClientError as e:
        logging.warning(f"Could not enable monitoring: {e}")

def main():
    logging.info("Starting Langraph website deployment on AWS EC2...")

    # Step 1: Launch EC2 instance
    instance = launch_ec2_instance()

    # Step 2: Setup monitoring
    setup_monitoring(instance.id)

    # Step 3: Wait for web server to be ready
    logging.info("Waiting for web server to start...")
    time.sleep(60)  # Wait for user-data script to finish

    # Step 4: Test website
    public_ip = instance.public_ip_address
    logging.info(f"Website should be available at: http://{public_ip}/")
    logging.info("AI-powered features available at: http://{}/ai (POST JSON: {{'text': 'your input'}})".format(public_ip))
    logging.info("Deployment complete. Monitor instance in AWS Console for resource usage and uptime.")

if __name__ == "__main__":
    main()