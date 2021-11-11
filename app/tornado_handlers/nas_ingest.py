"""
Tornado handler for the upload page
"""

from __future__ import print_function
import datetime
import os
from html import escape
import sys
import uuid
import binascii
import sqlite3
import traceback
import zipfile
import tornado.web
from tornado.ioloop import IOLoop
import subprocess

from pyulog import ULog
from pyulog.px4 import PX4ULog

# this is needed for the following imports
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), '../plot_app'))
from db_entry import DBVehicleData, DBData
from config import get_db_filename, get_http_protocol, get_domain_name, \
    email_notifications_config
from helper import get_total_flight_time, validate_url, get_log_filename, \
    load_ulog_file, get_airframe_name, ULogException
from overview_generator import generate_overview_img_from_id

#pylint: disable=relative-beyond-top-level
from .common import get_jinja_env, CustomHTTPError, generate_db_data_from_log_file, \
    TornadoRequestHandlerBase
from .send_email import send_notification_email, send_flightreport_email
from .multipart_streamer import MultiPartStreamer


# UPLOAD_TEMPLATE = 'bulk_upload.html'


#pylint: disable=attribute-defined-outside-init,too-many-statements, unused-argument


# @tornado.web.stream_request_body
class NASIngestHandler(TornadoRequestHandlerBase):
    """ Upload log file Tornado request handler: handles page requests and POST
    data """

    # def initialize(self):
    #     """ initialize the instance """
    #     self.multipart_streamer = None

    # def prepare(self):
    #     """ called before a new request """
    #     if self.request.method.upper() == 'POST':
    #         if 'expected_size' in self.request.arguments:
    #             self.request.connection.set_max_body_size(
    #                 int(self.get_argument('expected_size')))
    #         try:
    #             total = int(self.request.headers.get("Content-Length", "0"))
    #         except KeyError:
    #             total = 0
    #         self.multipart_streamer = MultiPartStreamer(total)

    # def data_received(self, chunk):
    #     """ called whenever new data is received """
    #     if self.multipart_streamer:
    #         self.multipart_streamer.data_received(chunk)

    # def get(self, *args, **kwargs):
    #     """ GET request callback """
    #     template = get_jinja_env().get_template(UPLOAD_TEMPLATE)
    #     self.write(template.render())

    def post(self, *args, **kwargs):
        """ POST request callback """
        print("Running NAS ingestion...")
        subprocess.Popen(["/home/hpe-server/bin/perform_log_upload.sh"], stdout=sys.stdout)
        self.write("Completed ingesting NAS ulogs...")
