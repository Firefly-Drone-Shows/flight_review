from bokeh.application.handlers.directory import DirectoryHandler
from tornado.web import RequestHandler

from .common import get_jinja_env

LOGIN_COOKIE_NAME = "flight_review_login"
LOGIN_TEMPLATE = 'login.html'

class LoginHandler(RequestHandler):
    def get(self):
        template = get_jinja_env().get_template(LOGIN_TEMPLATE)
        self.write(template.render())

    def post(self):
        password = self.get_body_argument("password")
        if password == "Password":  # Replace with real password
            self.set_secure_cookie(LOGIN_COOKIE_NAME, "true", expires_days=None)
            self.redirect("/upload")
        else:
            template = get_jinja_env().get_template(LOGIN_TEMPLATE)
            self.write(template.render(error="Incorrect password. Try again."))

class LogoutHandler(RequestHandler):
    def get(self):
        self.clear_cookie(LOGIN_COOKIE_NAME)
        self.redirect("/login")

class AuthMixin:
    def prepare(self):
        print("In Prepare")
        print(self.get_secure_cookie(LOGIN_COOKIE_NAME))
        if not self.get_secure_cookie(LOGIN_COOKIE_NAME):
            self.redirect("/login")

class AuthenticatedDirectoryHandler(AuthMixin, DirectoryHandler):
    pass