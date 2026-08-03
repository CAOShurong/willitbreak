"""A small piece of code that uses urllib3, the way anyone would.

Two of these calls stop working in urllib3 2.0. The rest are fine, and the
whole point is that a tool should be able to tell you which is which.
"""

import urllib3
from urllib3 import HTTPResponse, PoolManager

http = PoolManager()


def fetch(url):
    return http.request("GET", url).data


def wrap(body, headers):
    return HTTPResponse(body, headers, strict=True)


def adapt(raw_response):
    return urllib3.HTTPResponse.from_httplib(raw_response)


def parse(url):
    return urllib3.util.parse_url(url)
