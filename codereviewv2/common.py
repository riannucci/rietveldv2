from framework.rest_handler import xsrf

from . import auth_models

xsrf.GET_CURRENT_USER = auth_models.get_current_user

API_PREFIX = 'codereview/api/v2'
