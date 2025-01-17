#! /usr/bin/env python3
""" Script to run the bokeh server """

from __future__ import absolute_import
from __future__ import print_function

import argparse
import os
import sys
import errno
import sqlite3
import types
import shutil

from bokeh.application import Application
from bokeh.server.server import Server
from bokeh.application.handlers import DirectoryHandler



# this is needed for the following imports
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'plot_app'))
from tornado.web import StaticFileHandler
from tornado.web import RedirectHandler
from tornado_handlers.download import DownloadHandler
from tornado_handlers.bulk_upload import BulkUploadHandler, save_uploaded_log
from tornado_handlers.upload import UploadHandler
from tornado_handlers.browse import BrowseHandler, BrowseDataRetrievalHandler
from tornado_handlers.edit_entry import EditEntryHandler
from tornado_handlers.db_info_json import DBInfoHandler
from tornado_handlers.three_d import ThreeDHandler
from tornado_handlers.radio_controller import RadioControllerHandler
from tornado_handlers.error_labels import UpdateErrorLabelHandler
from tornado_handlers.nas_ingest import NASIngestHandler

from helper import set_log_id_is_filename, print_cache_info, ULogException #pylint: disable=C0411
from config import debug_print_timing, get_overview_img_filepath, get_db_filename #pylint: disable=C0411

#pylint: disable=invalid-name

def _fixup_deprecated_host_args(arguments):
    # --host is deprecated since bokeh 0.12.5. You might want to use
    # --allow-websocket-origin instead
    if arguments.host is not None and len(arguments.host) > 0:
        if arguments.allow_websocket_origin is None:
            arguments.allow_websocket_origin = []
        arguments.allow_websocket_origin += arguments.host
        arguments.allow_websocket_origin = list(set(arguments.allow_websocket_origin))

parser = argparse.ArgumentParser(description='Start bokeh Server')

parser.add_argument('-s', '--show', dest='show', action='store_true',
                    help='Open browser on startup')
parser.add_argument('--use-xheaders', action='store_true',
                    help="Prefer X-headers for IP/protocol information")
parser.add_argument('-f', '--file', metavar='file.ulg', action='store',
                    help='Directly show an ULog file, only for local use (implies -s)',
                    default=None)
parser.add_argument('--bulk-upload', metavar='ULOGFOLDER', action='store', dest = 'bulkupload',
                    help='Upload an entire folder of ULog files, then exit.')
parser.add_argument('--delete-after-bulk', action='store_true', dest = 'deleteafterbulk',
                    help='Only useful in combination with --bulk-upload. Deletes the ulog file after successfully ingesting it.')
parser.add_argument('--3d', dest='threed', action='store_true',
                    help='Open 3D page (only if --file is provided)')
parser.add_argument('--pid-analysis', dest='pid_analysis', action='store_true',
                    help='Open PID analysis page (only if --file is provided)')
parser.add_argument('--num-procs', dest='numprocs', type=int, action='store',
                    help="""Number of worker processes. Default to 1.
                    0 will autodetect number of cores""",
                    default=1)
parser.add_argument('--port', type=int, action='store',
                    help='Port to listen on', default=None)
parser.add_argument('--address', action='store',
                    help='Network address to listen to', default=None)
parser.add_argument('--host', action='append', type=str, metavar='HOST[:PORT]',
                    help="""Hosts whitelist, that must match the Host header in new
                    requests. It has the form <host>[:<port>]. If no port is specified, 80
                    is used. You should use the DNS name of the public endpoint here. \'*\'
                    matches all hosts (for testing only) (default=localhost)""",
                    default=None)
parser.add_argument('--allow-websocket-origin', action='append', type=str, metavar='HOST[:PORT]',
                    help="""Public hostnames which may connect to the Bokeh websocket""",
                    default=None)

args = parser.parse_args()

# This should remain here until --host is removed entirely
_fixup_deprecated_host_args(args)

applications = {}
main_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'plot_app')
handler = DirectoryHandler(filename=main_path)
applications['/plot_app'] = Application(handler)

server_kwargs = {}
if args.port is not None: server_kwargs['port'] = args.port
if args.use_xheaders: server_kwargs['use_xheaders'] = args.use_xheaders
server_kwargs['num_procs'] = args.numprocs
if args.address is not None: server_kwargs['address'] = args.address
if args.host is not None: server_kwargs['host'] = args.host
if args.allow_websocket_origin is not None:
    server_kwargs['allow_websocket_origin'] = args.allow_websocket_origin
server_kwargs['websocket_max_message_size'] = 100 * 1024 * 1024

# increase the maximum upload size (default is 100MB)
server_kwargs['http_server_kwargs'] = {'max_buffer_size': 300 * 1024 * 1024}

# turn on debug mode
server_kwargs['debug'] = True


show_ulog_file = False
show_3d_page = False
show_pid_analysis_page = False
if args.file is not None:
    ulog_file = os.path.abspath(args.file)
    show_ulog_file = True
    args.show = True
    show_3d_page = args.threed
    show_pid_analysis_page = args.pid_analysis

set_log_id_is_filename(show_ulog_file)


# additional request handlers
extra_patterns = [
    (r'/bulk_upload', BulkUploadHandler),
    (r'/upload', BulkUploadHandler),
    (r'/browse', BrowseHandler),
    (r'/browse_data_retrieval', BrowseDataRetrievalHandler),
    (r'/3d', ThreeDHandler),
    (r'/radio_controller', RadioControllerHandler),
    (r'/edit_entry', EditEntryHandler),
    (r'/?', BulkUploadHandler), #root should point to upload
    (r'/download', DownloadHandler),
    (r'/dbinfo', DBInfoHandler),
    (r'/error_label', UpdateErrorLabelHandler),
    (r"/stats", RedirectHandler, {"url": "/plot_app?stats=1"}),
    (r'/overview_img/(.*)', StaticFileHandler, {'path': get_overview_img_filepath()}),
    (r'/nas_ingest', NASIngestHandler)
]

# TODO: DON'T DO THIS
def _move_file_monkeypatch(self, path):
    shutil.copy(self.name, path)
if args.bulkupload:
    folder_path = os.path.abspath(args.bulkupload)
    if os.path.isdir(folder_path):
        folder_gen = os.walk(folder_path)
    else:
        folder_gen = [(os.path.dirname(folder_path), [], [os.path.basename(folder_path)])]
    con = sqlite3.connect(get_db_filename())
    cur = con.cursor()
    print(f"folder gen: {list(folder_gen)}")
    for root, dirs, files in folder_gen:
        for file_name in files:
            file_path = os.path.join(root, file_name)
            successful_ingestion = False
            with open(file_path, 'r') as file:
                # TODO: do actual validation here, don't just check filename
                _, ext = os.path.splitext(file_name)
                if ext not in ['.ulg', '.ulog']:
                    print(f'Skipping non-ULog file {file_path}')
                    continue
                # TODO: PLEASE don't do this, make save_uploaded_log work with real file-like objects
                file.move = types.MethodType(_move_file_monkeypatch, file) 
                file.get_filename = types.MethodType(lambda self: file_name, file)
                formdict = {}
                formdict['description'] = ''
                formdict['email'] = ''
                formdict['upload_type'] = 'personal'
                formdict['source'] = 'bulk'
                formdict['title'] = ''
                formdict['obfuscated'] = 0
                formdict['allow_for_analysis'] = 1
                formdict['feedback'] = ''
                formdict['wind_speed'] = -1
                formdict['rating'] = ''
                formdict['video_url'] = ''
                formdict['is_public'] = 1
                formdict['vehicle_name'] = ''
                formdict['error_labels'] = ''
                
                try:
                    log_id = save_uploaded_log(con, cur, file, formdict)
                    print('/plot_app?log='+log_id)
                    successful_ingestion = True
                except ULogException:
                    print("ULog error caused by file "+file_path)
            if successful_ingestion and args.deleteafterbulk:
                print('Successful ingestion! Deleting '+file_path)
                os.remove(file_path)
    cur.close()
    con.close()
    sys.exit(0)

server = None
custom_port = 5006
while server is None:
    try:
        server = Server(applications, extra_patterns=extra_patterns, **server_kwargs)
    except OSError as e:
        # if we get a port bind error and running locally with '-f',
        # automatically select another port (useful for opening multiple logs)
        if e.errno == errno.EADDRINUSE and show_ulog_file:
            custom_port += 1
            server_kwargs['port'] = custom_port
        else:
            raise

if args.show:
    # we have to defer opening in browser until we start up the server
    def show_callback():
        """ callback to open a browser window after server is fully initialized"""
        if show_ulog_file:
            if show_3d_page:
                server.show('/3d?log='+ulog_file)
            elif show_pid_analysis_page:
                server.show('/plot_app?plots=pid_analysis&log='+ulog_file)
            else:
                server.show('/plot_app?log='+ulog_file)
        else:
            server.show('/upload')
    server.io_loop.add_callback(show_callback)


if debug_print_timing():
    def print_statistics():
        """ print ulog cache info once per hour """
        print_cache_info()
        server.io_loop.call_later(60*60, print_statistics)
    server.io_loop.call_later(60, print_statistics)

# run_until_shutdown has been added 0.12.4 and is the preferred start method
run_op = getattr(server, "run_until_shutdown", None)
if callable(run_op):
    server.run_until_shutdown()
else:
    server.start()

