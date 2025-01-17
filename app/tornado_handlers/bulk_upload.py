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


UPLOAD_TEMPLATE = 'bulk_upload.html'


#pylint: disable=attribute-defined-outside-init,too-many-statements, unused-argument

def save_uploaded_log(con,cur,ulog_file,formdict):
    """
    Save a log that's already persisted on the filesystem into the database and into a folder we control.
    :param con: DB connection
    :param cur: DB cursor
    :param ulog_file: File-like object containing ULog
    :param formdict: Dict of options passed from upload page
    :param preserve_old_files: (Default False) Whether to leave the persisted copy on disk
    :return log_id: ID of the newly saved ULog file
    """
    # generate a log ID and persistence filename
    while True:
        log_id = str(uuid.uuid4())
        new_file_name = get_log_filename(log_id)
        if not os.path.exists(new_file_name):
            break
    # if preserve_old_files:
    #     print('Copying old file to', new_file_name)
    #     ulog_file.copy(new_file_name)
    # else:
    print('Moving uploaded file to', new_file_name)
    ulog_file.move(new_file_name)
    # Load the ulog file but only if not uploaded via CI.
    ulog = None
    if formdict['source'] != 'CI':
        ulog_file_name = get_log_filename(log_id)
        ulog = load_ulog_file(ulog_file_name)
    # generate a token: secure random string (url-safe)
    token = str(binascii.hexlify(os.urandom(16)), 'ascii')
    # put additional data into a DB
    cur.execute(
        'insert into Logs (Id, Title, Description, '
        'OriginalFilename, Date, AllowForAnalysis, Obfuscated, '
        'Source, Email, WindSpeed, Rating, Feedback, Type, '
        'videoUrl, ErrorLabels, Public, Token) values '
        '(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [log_id, formdict['title'], formdict['description'], ulog_file.get_filename(),
        datetime.datetime.now(), 1,
        0, formdict['source'], formdict['email'], formdict['wind_speed'], formdict['rating'],
        formdict['feedback'], formdict['upload_type'], formdict['video_url'], formdict['error_labels'], formdict['is_public'], token])
    if ulog is not None:
        vehicle_data = update_vehicle_db_entry(cur, ulog, log_id, formdict['vehicle_name'])
        vehicle_name = vehicle_data.name
    con.commit()
    generate_db_data_from_log_file(log_id, con)
    con.commit()
    return log_id

def update_vehicle_db_entry(cur, ulog, log_id, vehicle_name):
    """
    Update the Vehicle DB entry
    :param cur: DB cursor
    :param ulog: ULog object
    :param vehicle_name: new vehicle name or '' if not updated
    :return vehicle_data: DBVehicleData object
    """

    vehicle_data = DBVehicleData()
    if 'sys_uuid' in ulog.msg_info_dict:
        vehicle_data.uuid = escape(ulog.msg_info_dict['sys_uuid'])

        if vehicle_name == '':
            cur.execute('select Name '
                        'from Vehicle where UUID = ?', [vehicle_data.uuid])
            db_tuple = cur.fetchone()
            if db_tuple is not None:
                vehicle_data.name = db_tuple[0]
            print('reading vehicle name from db:'+vehicle_data.name)
        else:
            vehicle_data.name = vehicle_name
            print('vehicle name from uploader:'+vehicle_data.name)

        vehicle_data.log_id = log_id
        flight_time = get_total_flight_time(ulog)
        if flight_time is not None:
            vehicle_data.flight_time = flight_time

        # update or insert the DB entry
        cur.execute('insert or replace into Vehicle (UUID, LatestLogId, Name, FlightTime)'
                    'values (?, ?, ?, ?)',
                    [vehicle_data.uuid, vehicle_data.log_id, vehicle_data.name,
                     vehicle_data.flight_time])
    return vehicle_data


@tornado.web.stream_request_body
class BulkUploadHandler(TornadoRequestHandlerBase):
    """ Upload log file Tornado request handler: handles page requests and POST
    data """

    def initialize(self):
        """ initialize the instance """
        self.multipart_streamer = None

    def prepare(self):
        """ called before a new request """
        if self.request.method.upper() == 'POST':
            if 'expected_size' in self.request.arguments:
                self.request.connection.set_max_body_size(
                    int(self.get_argument('expected_size')))
            try:
                total = int(self.request.headers.get("Content-Length", "0"))
            except KeyError:
                total = 0
            self.multipart_streamer = MultiPartStreamer(total)

    def data_received(self, chunk):
        """ called whenever new data is received """
        if self.multipart_streamer:
            self.multipart_streamer.data_received(chunk)

    def get(self, *args, **kwargs):
        """ GET request callback """
        template = get_jinja_env().get_template(UPLOAD_TEMPLATE)
        self.write(template.render())

    def post(self, *args, **kwargs):
        """ POST request callback """
        if self.multipart_streamer:
            try:
                self.multipart_streamer.data_complete()
                form_data = self.multipart_streamer.get_values(
                    ['description', 'email',
                     'allowForAnalysis', 'obfuscated', 'source', 'type',
                     'feedback', 'windSpeed', 'rating', 'videoUrl', 'public',
                     'vehicleName'])

                description = escape(form_data['description'].decode("utf-8"))
                email = form_data.get('email', bytes("(no email provided)", 'utf-8')).decode("utf-8")
                upload_type = form_data.get('type', bytes("personal", 'utf-8')).decode("utf-8")
                source = form_data.get('source', bytes("webui", 'utf-8')).decode("utf-8")
                title = '' # may be used in future...
                obfuscated = {'true': 1, 'false': 0}.get(form_data.get('obfuscated', b'false').decode('utf-8'), 0)
                allow_for_analysis = {'true': 1, 'false': 0}.get(form_data.get('allowForAnalysis', b'false').decode('utf-8'), 0)
                feedback = escape(form_data.get('feedback', b'').decode("utf-8"))

                wind_speed = -1
                rating = ''
                video_url = ''
                is_public = 1
                vehicle_name = escape(form_data.get('vehicleName', bytes("", 'utf-8')).decode("utf-8"))
                error_labels = ''

                # TODO: make the format of formdict a little more compatible with form_data above
                formdict = {}
                formdict['description'] = description
                formdict['email'] = email
                formdict['upload_type'] = upload_type
                formdict['source'] = source
                formdict['title'] = title
                formdict['obfuscated'] = obfuscated
                formdict['allow_for_analysis'] = allow_for_analysis
                formdict['feedback'] = feedback
                formdict['wind_speed'] = wind_speed
                formdict['rating'] = rating
                formdict['video_url'] = video_url
                formdict['is_public'] = is_public
                formdict['vehicle_name'] = vehicle_name
                formdict['error_labels'] = error_labels

                # we don't bother parsing any of the "flight report" metadata, it's not very useful to us
                # stored_email = ''
                # if upload_type == 'flightreport':
                #     try:
                #         wind_speed = int(escape(form_data['windSpeed'].decode("utf-8")))
                #     except ValueError:
                #         wind_speed = -1
                #     rating = escape(form_data['rating'].decode("utf-8"))
                #     if rating == 'notset': rating = ''
                #     stored_email = email
                #     # get video url & check if valid
                #     video_url = escape(form_data['videoUrl'].decode("utf-8"), quote=True)
                #     if not validate_url(video_url):
                #         video_url = ''
                #     if 'vehicleName' in form_data:
                #         vehicle_name = escape(form_data['vehicleName'].decode("utf-8"))

                #     # always allow for statistical analysis
                #     allow_for_analysis = 1
                #     if 'public' in form_data:
                #         if form_data['public'].decode("utf-8") == 'true':
                #             is_public = 1

                # open the database connection
                con = sqlite3.connect(get_db_filename())
                cur = con.cursor()


                file_obj = self.multipart_streamer.get_parts_by_name('filearg')[0]
                upload_file_name = file_obj.get_filename()

                # read file header and ensure validity
                peek_ulog_header = file_obj.get_payload_partial(len(ULog.HEADER_BYTES))
                peek_zip_header = file_obj.get_payload_partial(4)
                zip_headers = [b'\x50\x4b\x03\x04', b'\x50\x4b\x05\x06', b'\x50\x4b\x07\x08']
                # we check that it is either a well formed zip or ULog
                # is file a ULog? then continue as we were :)
                if (peek_ulog_header == ULog.HEADER_BYTES):
                    log_id = save_uploaded_log(con, cur, file_obj, formdict)


                    # generate URL info and redirect
                    url = '/plot_app?log='+log_id
                    full_plot_url = get_http_protocol()+'://'+get_domain_name()+url
                    print(full_plot_url)
                    # do not redirect for QGC
                    if source != 'QGroundControl':
                        self.redirect(url)

                # is the file a zip? read the magic numbers and unzip it
                elif (peek_zip_header in zip_headers):
                    with zipfile.ZipFile(file_obj.f_out) as zip:
                        for log_filename in zip.namelist():
                            # make sure we're dealing with a ulog file
                            # TODO: do actual validation here, don't just check filename
                            _, ext = os.path.splitext(log_filename)
                            if ext not in ['.ulg', '.ulog']:
                                print(f'Skipping extracting non-ULog file {file_obj.f_out.name}//{log_filename}')
                                continue
                            # TODO: switch to save_uploaded_log
                            # generate a log ID and persistence filename
                            while True:
                                log_id = str(uuid.uuid4())
                                new_file_name = get_log_filename(log_id)
                                if not os.path.exists(new_file_name):
                                    break
                            # extract and rename the ulog file to something we control
                            print(f'Extracting uploaded log {file_obj.f_out.name}//{log_filename} file to', new_file_name)
                            zip.extract(log_filename, path = os.path.dirname(new_file_name))
                            os.rename(os.path.join(os.path.dirname(new_file_name), log_filename), new_file_name)
                            # Load the ulog file but only if not uploaded via CI.
                            ulog = None
                            if source != 'CI':
                                ulog_file_name = get_log_filename(log_id)
                                ulog = load_ulog_file(ulog_file_name)
                            # generate a token: secure random string (url-safe)
                            token = str(binascii.hexlify(os.urandom(16)), 'ascii')
                            # put additional data into a DB
                            cur.execute(
                                'insert into Logs (Id, Title, Description, '
                                'OriginalFilename, Date, AllowForAnalysis, Obfuscated, '
                                'Source, Email, WindSpeed, Rating, Feedback, Type, '
                                'videoUrl, ErrorLabels, Public, Token) values '
                                '(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                [log_id, title, description, upload_file_name,
                                datetime.datetime.now(), allow_for_analysis,
                                obfuscated, source, email, wind_speed, rating,
                                feedback, upload_type, video_url, error_labels, is_public, token])
                            if ulog is not None:
                                vehicle_data = update_vehicle_db_entry(cur, ulog, log_id, vehicle_name)
                                vehicle_name = vehicle_data.name
                            con.commit()
                            generate_db_data_from_log_file(log_id, con)
                            con.commit()

                            # generate URL info and redirect
                            url = '/plot_app?log='+log_id
                            full_plot_url = get_http_protocol()+'://'+get_domain_name()+url
                            print(full_plot_url)
                        self.redirect('/browse')
                # is file neither a zip nor a ULog? error out :)
                else:
                    if upload_file_name[-7:].lower() == '.px4log':
                        raise CustomHTTPError(
                            400,
                            'Invalid File. This seems to be a px4log file. '
                            'Upload it to <a href="http://logs.uaventure.com" '
                            'target="_blank">logs.uaventure.com</a>.')
                    raise CustomHTTPError(400, 'Invalid File')




                # this massive chunk of comment was the code used to send emails for 
                # uploaded flight reports. we no longer use this functionality.
                # however, for some weird reason, this chunk of code also generated a
                # LogsGenerated entry for faster log loading for public logs. so 
                # we move the line up and out of the code it's not supposed to be a part
                # of, and put it right here :)
                #generate_db_data_from_log_file(log_id, con)

                # delete_url = get_http_protocol()+'://'+get_domain_name()+ \
                #     '/edit_entry?action=delete&log='+log_id+'&token='+token

                # information for the notification email
                # info = {}
                # info['description'] = description
                # info['feedback'] = feedback
                # info['upload_filename'] = upload_file_name
                # info['type'] = ''
                # info['airframe'] = ''
                # info['hardware'] = ''
                # info['uuid'] = ''
                # info['software'] = ''
                # info['rating'] = rating
                # if len(vehicle_name) > 0:
                #     info['vehicle_name'] = vehicle_name

                # if ulog is not None:
                #     px4_ulog = PX4ULog(ulog)
                #     info['type'] = px4_ulog.get_mav_type()
                #     airframe_name_tuple = get_airframe_name(ulog)
                #     if airframe_name_tuple is not None:
                #         airframe_name, airframe_id = airframe_name_tuple
                #         if len(airframe_name) == 0:
                #             info['airframe'] = airframe_id
                #         else:
                #             info['airframe'] = airframe_name
                #     sys_hardware = ''
                #     if 'ver_hw' in ulog.msg_info_dict:
                #         sys_hardware = escape(ulog.msg_info_dict['ver_hw'])
                #         info['hardware'] = sys_hardware
                #     if 'sys_uuid' in ulog.msg_info_dict and sys_hardware != 'SITL':
                #         info['uuid'] = escape(ulog.msg_info_dict['sys_uuid'])
                #     branch_info = ''
                #     if 'ver_sw_branch' in ulog.msg_info_dict:
                #         branch_info = ' (branch: '+ulog.msg_info_dict['ver_sw_branch']+')'
                #     if 'ver_sw' in ulog.msg_info_dict:
                #         ver_sw = escape(ulog.msg_info_dict['ver_sw'])
                #         info['software'] = ver_sw + branch_info


                # if upload_type == 'flightreport' and is_public and source != 'CI':
                #     destinations = set(email_notifications_config['public_flightreport'])
                #     if rating in ['unsatisfactory', 'crash_sw_hw', 'crash_pilot']:
                #         destinations = destinations | \
                #             set(email_notifications_config['public_flightreport_bad'])
                #     send_flightreport_email(
                #         list(destinations),
                #         full_plot_url,
                #         DBData.rating_str_static(rating),
                #         DBData.wind_speed_str_static(wind_speed), delete_url,
                #         stored_email, info)

                #     # also generate the additional DB entry
                #     # (we may have the log already loaded in 'ulog', however the
                #     # lru cache will make it very quick to load it again)
                #     generate_db_data_from_log_file(log_id, con)
                #     # also generate the preview image
                #     IOLoop.instance().add_callback(generate_overview_img_from_id, log_id)

                # send notification emails
                # send_notification_email(email, full_plot_url, delete_url, info)


            except CustomHTTPError:
                raise

            except ULogException as e:
                raise CustomHTTPError(
                    400,
                    'Failed to parse the file. It is most likely corrupt.') from e
            except Exception as e:
                print('Fatal error when handling POST data', sys.exc_info()[0],
                      sys.exc_info()[1])
                traceback.print_exc()
                raise CustomHTTPError(500) from e

            finally:
                # close our DB connections
                cur.close()
                con.close()
                # free the uploaded files
                self.multipart_streamer.release_parts()

