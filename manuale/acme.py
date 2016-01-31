"""
ACME API client.
"""

import copy
from collections import namedtuple
from urllib.parse import urljoin, urlparse

import requests

from . import __version__
from .crypto import generate_header, sign_request
from .errors import *

DEFAULT_HEADERS = {
    'User-Agent': "manuale {} (https://github.com/veeti/manuale)".format(__version__),
}

class Acme:

    def __init__(self, url, account):
        self.url = url
        self.account = account
        self.key = account.key

    def get_nonce(self):
        """
        Gets a new nonce.
        """
        return self.get('/directory').headers.get('Replay-Nonce')

    def get_headers(self):
        """
        Builds a new pair of headers for signed requests.
        """
        header = generate_header(self.key)
        protected_header = copy.deepcopy(header)
        protected_header['nonce'] = self.get_nonce()
        return header, protected_header

    def register(self, email):
        """
        Registers the current account on the server.
        """
        header, protected_header = self.get_headers()
        request = sign_request(self.key, header, protected_header, {
            'resource': 'new-reg',
            'contact': [
                "mailto:{}".format(email),
            ],
        })

        response = self.post('/acme/new-reg', request)
        uri = response.headers.get('Location')
        if response.status_code == 201:
            self.account.uri = uri

            # Find terms of service from link headers
            terms = _get_link(response.headers.get('Link'), 'terms-of-service')

            return RegistrationResult(
                contents=_json(response),
                uri=uri,
                terms=terms
            )
        elif response.status_code == 409:
            raise AccountAlreadyExistsError(response, uri)
        raise AcmeError(response)

    def get_registration(self):
        """
        Gets available account information from the server.
        """
        header, protected_header = self.get_headers()
        request = sign_request(self.key, header, protected_header, {
            'resource': 'reg'
        })

        response = self.post(self.account.uri, request)
        if str(response.status_code).startswith('2'):
            return _json(response)
        raise AcmeError(response)

    def update_registration(self, params=None):
        """
        Updates registration information on the server.
        """
        params = params or {}
        params['resource'] = 'reg'
        header, protected_header = self.get_headers()
        request = sign_request(self.key, header, protected_header, params)

        response = self.post(self.account.uri, request)
        if str(response.status_code).startswith('2'):
            return True
        raise AcmeError(response)

    def new_authorization(self, domain):
        """
        Requests a new authorization for the specified domain.
        """
        header, protected_header = self.get_headers()
        request = sign_request(self.key, header, protected_header, {
            'resource': 'new-authz',
            'identifier': { 'type': 'dns', 'value': domain },
        })

        response = self.post('/acme/new-authz', request)
        if response.status_code == 201:
            return NewAuthorizationResult(_json(response), response.headers.get('Location'))
        raise AcmeError(response)

    def validate_authorization(self, uri, _type, key_authorization):
        """
        Marks the specified validation as complete.
        """
        header, protected_header = self.get_headers()
        request = sign_request(self.key, header, protected_header, {
            'resource': 'challenge',
            'type': _type,
            'keyAuthorization': key_authorization,
        })

        response = self.post(uri, request)
        if str(response.status_code).startswith('2'):
            return True
        raise AcmeError(response)

    def get_authorization(self, uri):
        """
        Returns the authorization status.
        """
        response = self.get(uri)
        try:
            return response.json()
        except (ValueError, TypeError, AttributeError) as e:
            raise AcmeError(e)

    def issue_certificate(self, csr):
        header, protected = self.get_headers()
        http_headers = { 'Accept': 'application/pkix-cert' }
        request = sign_request(self.key, header, protected, {
            'resource': 'new-cert', 'csr': csr,
        })

        response = self.post('/acme/new-cert', request, headers=http_headers)
        if response.status_code == 201:
            # Get the issuer certificate
            chain = _get_link(response.headers.get('Link'), 'up')
            if chain:
                chain = requests.get(chain, headers=DEFAULT_HEADERS).content

            return IssuanceResult(
                response.content,
                response.headers.get('Location'),
                chain,
            )
        raise AcmeError(response)

    def get(self, path, headers=None):
        _headers = DEFAULT_HEADERS.copy()
        if headers:
            _headers.update(headers)
        return requests.get(self.path(path), headers=_headers)

    def post(self, path, body, headers=None):
        _headers = DEFAULT_HEADERS.copy()
        _headers['Content-Type'] = 'application/json'
        if headers:
            _headers.update(headers)
        return requests.post(self.path(path), data=body, headers=_headers)

    def path(self, path):
        # Make sure path is relative
        if path.startswith('http'):
            path = urlparse(path).path
        return urljoin(self.url, path)


RegistrationResult = namedtuple('RegistrationResult', 'contents uri terms')
NewAuthorizationResult = namedtuple('NewAuthorizationResult', 'contents uri')
IssuanceResult = namedtuple('IssuanceResult', 'certificate location intermediate')


def _get_link(header, type):
    links = (header or '').split(',')
    for link in links:
        link = link.strip()
        if type in link:
            return link[1:link.index('>')]

def _json(response):
    try:
        return response.json()
    except ValueError as e:
        raise AcmeError("Invalid JSON response. {}".format(e))