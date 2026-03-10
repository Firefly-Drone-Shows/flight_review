"""
Tornado handler to delete a log entry (authenticated, no token required)
"""

import os
import sqlite3
import sys
import tornado.web

sys.path.append(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "../plot_app")
)
from config import get_db_filename, get_kml_filepath, get_overview_img_filepath
from helper import clear_ulog_cache, get_log_filename

from .auth import AuthMixin


class DeleteLogHandler(AuthMixin, tornado.web.RequestHandler):
    """Delete a log entry, requires authentication"""

    def post(self):
        log_id = self.get_body_argument("log")

        if self._delete_log(log_id):
            self.redirect("/browse")
        else:
            self.set_status(400)
            self.write("Failed to delete log. It may not exist.")

    @staticmethod
    def _delete_log(log_id):
        """
        Delete a log entry (DB & files) without token validation.
        Authentication is enforced by AuthMixin.

        :return: True on success
        """
        con = sqlite3.connect(get_db_filename(), detect_types=sqlite3.PARSE_DECLTYPES)
        cur = con.cursor()
        cur.execute("SELECT Id FROM Logs WHERE Id = ?", (log_id,))
        if cur.fetchone() is None:
            cur.close()
            con.close()
            return False

        # kml file
        kml_path = get_kml_filepath()
        kml_file_name = os.path.join(kml_path, log_id.replace("/", ".") + ".kml")
        if os.path.exists(kml_file_name):
            os.unlink(kml_file_name)

        # preview image
        preview_image_filename = os.path.join(
            get_overview_img_filepath(), log_id + ".png"
        )
        if os.path.exists(preview_image_filename):
            os.unlink(preview_image_filename)

        # log file
        log_file_name = get_log_filename(log_id)
        if os.path.exists(log_file_name):
            os.unlink(log_file_name)

        cur.execute("DELETE FROM LogsGenerated WHERE Id = ?", (log_id,))
        cur.execute("DELETE FROM Logs WHERE Id = ?", (log_id,))
        con.commit()
        cur.close()
        con.close()

        clear_ulog_cache()
        return True
