#!/usr/bin/env python3

import argparse
import sys 
import boto3
import requests 
import getpass 
import configparser 
import base64 
import xml.etree.ElementTree as ET 
import os
from bs4 import BeautifulSoup 
from os.path import expanduser 
from urllib.parse import urlparse, urlunparse 
from requests_ntlm import HttpNtlmAuth

######
class ConfigSettings:
	def __init__(self, commandLineArguments):
		self.username = commandLineArguments.getUser() 
		self.domain = commandLineArguments.getDomain() 
		self.filter = commandLineArguments.getFilter()
		self.tokenDuration = commandLineArguments.getTokenDuration()
		self.profile = commandLineArguments.getProfile()
		if commandLineArguments.getAsk(): self.askUser()

	def askUser(self):
		self.domain = self.inputWithDefault('Enter domain ({}):', self.domain)
		self.username = self.inputWithDefault('Enter username ({}):', self.username)
		self.profile = self.inputWithDefault('Enter profile ({}):', self.profile)
		
	def inputWithDefault (self, display, default):
		value = input(display.format(default))
		if len(value) == 0: value = default
		return value
		
	def getTokenDuration(self):
		return int(self.tokenDuration)

	def getUsername(self): 
		return self.username + '@' + self.domain + '.rootdom.dk'

	def getFilter(self):
		return self.filter

	def getPassword(self): 
		return self.password 
 
	def askPassword(self): 
		self.password = getpass.getpass()
		

	def getProfile(self):
		return self.profile

class CommandLineArguments: 
	def __init__(self): 
		self.parser = argparse.ArgumentParser()
		self.parser.add_argument("-p", "--profile", help="Specify which profile name to store settings in.", default="default")
		self.parser.add_argument("-u", "--user", help="Specify username to user. Don't specify any domain information", default=getpass.getuser())
		self.parser.add_argument("-d", "--domain", help="Specify domain. Defaults to the userdomain environment and if not polhus", default=os.environ.get('userdomain', "polhus"))
		self.parser.add_argument("-a", "--ask", help="Ask user for all values. Defaults from other command line arguments", action='store_true')
		self.parser.add_argument("-td", "--tokenDuration", help="Token duration in seconds. Default is 3600 which is the default in AWS, but generally speaking longer durations are more convenient.", default="3600")
		self.parser.add_argument("-f", "--filter", help="Filter for returned role values. Specify full name (or unique match) to avoid selecting role and login directly.", default = "")
		self.args = self.parser.parse_args()

	def getProfile(self):
		return self.args.profile

	def getUser(self):
		return self.args.user

	def getDomain(self):
		return self.args.domain

	def getTokenDuration(self): 
		return self.args.tokenDuration

	def getAsk(self):
		return self.args.ask

	def getFilter(self):
		return self.args.filter


class AwsConfigFile:
	def __init__(self, profile, configfile = '/.aws/credentials', region = 'eu-west-1', outputformat = 'json'):
		self.__profile = profile
		# awsconfigfile: The file where this script will store the temp credentials under the saml profile 
		self.__awsconfigfile = configfile
		# region: The default AWS region that this script will connect to for all API calls 
		self.__region = region
		# output format: The AWS CLI output format that will be configured in the saml profile (affects subsequent CLI calls) 
		self.__outputformat = outputformat
		self.filename = expanduser("~") + configfile
		pass

	def update(self, credentials):
		"""Update configuration file with security credentials from STS client"""
		# Read in the existing config file
		config = configparser.RawConfigParser()
		config.read(self.filename)
		
		# Put the credentials into a specific profile instead of clobbering
		# the default credentials
		if not config.has_section(self.__profile):
			config.add_section(self.__profile)
		
		config.set(self.__profile, 'output', self.__outputformat)
		config.set(self.__profile, 'region', self.__region)
		config.set(self.__profile, 'aws_access_key_id', token['Credentials']['AccessKeyId'])
		config.set(self.__profile, 'aws_secret_access_key', token['Credentials']['SecretAccessKey'])
		config.set(self.__profile, 'aws_session_token', token['Credentials']['SessionToken'])
		
		# Write the updated config file
		with open(self.filename, 'w+') as configfile:
			config.write(configfile)

################################################################################
# Functions 

def getAssertionFromResponse(response):
	assertion = ''
	# Decode the response and extract the SAML assertion 
	soup = BeautifulSoup(response.text, features="html.parser") 
	# Look for the SAMLResponse attribute of the input tag (determined by 
	# analyzing the debug print(lines above) )
	for inputtag in soup.find_all('input'): 
	    if(inputtag.get('name') == 'SAMLResponse'): 
	        #print(inputtag.get('value')) 
	        assertion = inputtag.get('value')
	return assertion


def account_information(session, assertion):
	result = {}
	response = session.post("https://signin.aws.amazon.com/saml", data={"SAMLResponse": assertion})
	soup = BeautifulSoup(response.text, features="html.parser")
	account_elements = soup.find_all("div", class_="saml-account")
	for account_element in account_elements:
		account_name_element = account_element.find("div", class_="saml-account-name")
		if account_name_element is None:
			continue
		account_name = account_name_element.get_text().replace("Account: ", "")
		role_elements = account_element.find_all("div", class_="saml-role")
		for role_element in role_elements:
			role_name = role_element.find("label").get_text()
			role_arn = role_element.find("input")["value"]
			result[role_arn] = (account_name, role_name)
	return result	

################################################################################

##########################################################################
# Variables 
commandLineArguments = CommandLineArguments()
  
# SSL certificate verification: Whether or not strict certificate 
# verification is done, False should only be used for dev/test 
sslverification = True 
 
# idpentryurl: The initial URL that starts the authentication process. 
idpentryurl = 'https://sts.rootdom.dk/adfs/ls/IdpInitiatedSignOn.aspx?loginToRp=urn:amazon:webservices' 

##########################################################################

# Get the federated credentials from the user
settings = ConfigSettings(commandLineArguments)
print('')
print('Will use the username {}'.format(settings.getUsername()))
settings.askPassword()

# Initiate session handler 
session = requests.Session() 
 
# Programatically get the SAML assertion 
# Set up the NTLM authentication handler by using the provided credential 
#session.auth = HttpNtlmAuth(settings.getUsername(), settings.getPassword(), session) 
 
# Opens the initial AD FS URL and follows all of the HTTP302 redirects 
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:66.0) Gecko/20100101 Firefox/66.0'}
#response = session.get(idpentryurl, headers=headers, verify=sslverification) 
#session.auth = None
session.get(url = idpentryurl, headers = headers, verify = sslverification)
payload = {'UserName': settings.getUsername(), 'Password': settings.getPassword(), 'optionForms': 'FormsAuthentication' }
response = session.post(url = idpentryurl, headers=headers, verify=sslverification, data = payload)
 
# Debug the response if needed 
print(response)

assertion = getAssertionFromResponse(response)
 
# Parse the returned assertion and extract the authorized roles 
unfilteredawsroles = []
awsroles = []
try:
	print(base64.b64decode(assertion))
	root = ET.fromstring(base64.b64decode(assertion))
except: 
	print('An exception occurred using NTLM negotiation. retrying with post to sts.rootdom.dk')
#	payload = {'UserName': settings.getUsername(), 'Password': settings.getPassword(), 'optionForms': 'FormsAuthentication' }
#	session.auth = None
#	session.get(url = idpentryurl, headers = headers, verify = sslverification)
#	response = session.post(url = idpentryurl, headers=headers, verify=sslverification, data = payload)
#	assertion = getAssertionFromResponse(response)
#	root = ET.fromstring(base64.b64decode(assertion))
#	print(root)

# Overwrite and delete the credential variables, just for safety
username = '##############################################'
password = '##############################################'
del username
del password


for saml2attribute in root.iter('{urn:oasis:names:tc:SAML:2.0:assertion}Attribute'): 
    if (saml2attribute.get('Name') == 'https://aws.amazon.com/SAML/Attributes/Role'): 
        for saml2attributevalue in saml2attribute.iter('{urn:oasis:names:tc:SAML:2.0:assertion}AttributeValue'):
            unfilteredawsroles.append(saml2attributevalue.text)
 
# Note the format of the attribute value should be role_arn,principal_arn 
# but lots of blogs list it as principal_arn,role_arn so let's reverse 
# them if needed 
for awsrole in unfilteredawsroles:
    chunks = awsrole.split(',') 
    if'saml-provider' in chunks[0]:
        newawsrole = chunks[1] + ',' + chunks[0] 
        index = unfilteredawsroles.index(awsrole)
        unfilteredawsroles.insert(index, newawsrole)
        unfilteredawsroles.remove(awsrole)

for awsrole in unfilteredawsroles:
    if settings.getFilter() in awsrole:
        awsroles.insert(0, awsrole)

# If I have more than one role, ask the user which one they want, 
# otherwise just proceed 
print("" )
if len(awsroles) > 1:
    role_accounts = account_information(session, assertion)
    i = 0
    print("Please choose the role you would like to assume:" )
    for awsrole in awsroles:
        role_arn = awsrole.split(',')[0]
        account = role_accounts[role_arn]
        print('[', i, ']: ', awsrole.split(',')[0] + ' (' + account[0] + ')')
        i += 1 

    print("Selection: ", )
    selectedroleindex = input() 
 
    # Basic sanity check of input 
    if int(selectedroleindex) > (len(awsroles) - 1): 
        print('You selected an invalid role index, please try again' )
        sys.exit(0) 
 
    role_arn = awsroles[int(selectedroleindex)].split(',')[0] 
    principal_arn = awsroles[int(selectedroleindex)].split(',')[1]
 
elif len(awsroles) == 1: 
    role_arn = awsroles[0].split(',')[0] 
    principal_arn = awsroles[0].split(',')[1]
else:
    print ("No roles returned. (If you have provided a filter you might test the unfiltered result.")
    quit()

# Use the assertion to get an AWS STS token using Assume Role with SAML
conn = boto3.client('sts')
token = conn.assume_role_with_saml(RoleArn = role_arn, PrincipalArn = principal_arn, SAMLAssertion = assertion, DurationSeconds = settings.getTokenDuration())

print(token)
# Write the AWS STS token into the AWS credential file
awsCondigFile = AwsConfigFile(settings.getProfile())
awsCondigFile.update(token)

# Give the user some basic info as to what has just happened
print('\n\n----------------------------------------------------------------')
print('Your new access key pair has been stored in the AWS configuration file {0} under the saml profile.'.format(awsCondigFile.filename))
print('Note that it will expire at {0}.'.format(token['Credentials']['Expiration']))
print('After this time you may safely rerun this script to refresh your access key pair.')
print('To use this credential call the AWS CLI with the --profile option (e.g. aws --profile saml ec2 describe-instances).')
print('----------------------------------------------------------------\n\n')

iamid = token['AssumedRoleUser']['Arn']

print('Assumed role is:')
print(iamid)
