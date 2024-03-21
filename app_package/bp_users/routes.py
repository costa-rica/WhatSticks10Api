from flask import Blueprint
from flask import request, jsonify, make_response, current_app
from ws_models import session_scope, Users, OuraToken, OuraSleepDescriptions, AppleHealthQuantityCategory, \
    AppleHealthWorkout, UserLocationDay, Locations
from werkzeug.security import generate_password_hash, check_password_hash #password hashing
import bcrypt
from datetime import datetime
from itsdangerous.url_safe import URLSafeTimedSerializer#new 2023
import logging
import os
from logging.handlers import RotatingFileHandler
import json
import socket
from app_package.utilsDecorators import token_required, response_dict_tech_difficulties_alert
from app_package.bp_users.utils import send_confirm_email, send_reset_email, delete_user_from_table, \
    delete_user_data_files, get_apple_health_count_date
from sqlalchemy import desc
from ws_utilities import convert_lat_lon_to_timezone_string, convert_lat_lon_to_city_country, \
    find_user_location, add_user_loc_day_process
import requests


formatter = logging.Formatter('%(asctime)s:%(name)s:%(message)s')
formatter_terminal = logging.Formatter('%(asctime)s:%(filename)s:%(name)s:%(message)s')

logger_bp_users = logging.getLogger(__name__)
logger_bp_users.setLevel(logging.DEBUG)

file_handler = RotatingFileHandler(os.path.join(os.environ.get('API_ROOT'),'logs','users.log'), mode='a', maxBytes=5*1024*1024,backupCount=2)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter_terminal)

logger_bp_users.addHandler(file_handler)
logger_bp_users.addHandler(stream_handler)

bp_users = Blueprint('bp_users', __name__)
logger_bp_users.info(f'- WhatSticks10 API users Bluprints initialized')

salt = bcrypt.gensalt()

@bp_users.route('/are_we_working', methods=['GET'])
def are_we_working():
    logger_bp_users.info(f"are_we_working endpoint pinged")

    hostname = socket.gethostname()

    return jsonify(f"Yes! We're up! in the {hostname} machine")


@bp_users.route('/login',methods=['POST'])
def login():
    logger_bp_users.info(f"- login endpoint pinged -")
    #############################################################################################
    ## In case of emergency, ACTIVATE_TECHNICAL_DIFFICULTIES_ALERT prevents users from logging in
    if current_app.config.get('ACTIVATE_TECHNICAL_DIFFICULTIES_ALERT'):
        response_dict = response_dict_tech_difficulties_alert(response_dict = {})
        return jsonify(response_dict)
    #############################################################################################

    auth = request.authorization
    logger_bp_users.info(f"- auth.username: {auth.username} -")

    if not auth or not auth.username or not auth.password:
        logger_bp_users.info(f"- /login failed: if not auth or not auth.username or not auth.password")
        return make_response('Could not verify', 401)
    
    with session_scope() as session:
        user = session.query(Users).filter_by(email=auth.username).first()

        if not user:
            logger_bp_users.info(f"- /login failed: if not user:")
            return make_response('Could not verify - user not found', 401)

        if auth.password:
            if bcrypt.checkpw(auth.password.encode(), user.password.encode()):
                serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])

                user_object_for_swift_app = {
                    'id': str(user.id),
                    'email': user.email,
                    'username': user.username,
                    'token': serializer.dumps({'user_id': user.id}),
                    'timezone': user.timezone,
                    'location_permission': str(user.location_permission),
                    'location_reoccuring_permission': str(user.location_reoccuring_permission),
                }

                latest_entry = session.query(UserLocationDay).filter(
                    UserLocationDay.user_id == user.id
                ).order_by(desc(UserLocationDay.date_time_utc_user_check_in)).first()

                if latest_entry is not None:
                    user_object_for_swift_app['last_location_date'] = str(latest_entry.date_time_utc_user_check_in)[:10]

                oura_token_obj = session.query(OuraToken).filter_by(user_id=user.id).first()
                if oura_token_obj and oura_token_obj.token is not None:
                    user_object_for_swift_app['oura_token'] = oura_token_obj.token

                response_dict = {
                    'alert_title': "Success",
                    'alert_message': "",
                    'user': user_object_for_swift_app
                }

                logger_bp_users.info(f"- response_dict: {response_dict} -")
                return jsonify(response_dict)

    logger_bp_users.info(f"- /login failed: if auth.password:")
    return make_response('Could not verify', 401)


@bp_users.route('/register', methods=['POST'])
def register():
    logger_bp_users.info(f"- register endpoint pinged -")
    # ws_api_password = request.json.get('WS_API_PASSWORD')
    logger_bp_users.info(request.json)

    ######################################################################################
    ## In case of emergency, ACTIVATE_TECHNICAL_DIFFICULTIES_ALERT prevents new users
    if current_app.config.get('ACTIVATE_TECHNICAL_DIFFICULTIES_ALERT'):
        response_dict = response_dict_tech_difficulties_alert(response_dict = {})
        return jsonify(response_dict)
    ######################################################################################

    try:
        request_json = request.json
        logger_bp_users.info(f"successfully read request_json (new_email): {request_json.get('new_email')}")
        logger_bp_users.info(f"successfully read request_json: {request_json}")
    except Exception as e:
        logger_bp_users.info(f"failed to read json")
        logger_bp_users.info(f"{type(e).__name__}: {e}")
        response = jsonify({"error": str(e)})
        return make_response(response, 400)
    
    response_dict = {}

    if request_json.get('new_email') in ("", None) or request_json.get('new_password') in ("" , None):
        logger_bp_users.info(f"- failed register no email or password -")
        response_dict["alert_title"] = f"User must have email and password"
        response_dict["alert_message"] = f""
        response_dict = response_dict_tech_difficulties_alert(response_dict = {})
        return jsonify(response_dict)
    with session_scope() as session:
        user_exists = session.query(Users).filter_by(email= request_json.get('new_email')).first()

    if user_exists:
        logger_bp_users.info(f"- failed register user already exists -")
        response_dict["alert_title"] = f"User already exists"
        response_dict["alert_message"] = f"Try loggining in"
        response_dict = response_dict_tech_difficulties_alert(response_dict = {})
        return jsonify(response_dict)

    hash_pw = bcrypt.hashpw(request_json.get('new_password').encode(), salt)
    with session_scope() as session:
        new_user = Users()

        # lat = 999.999
        # lon = 999.999

        for key, value in request_json.items():
            if key == "new_password":
                setattr(new_user, "password", hash_pw)
            elif key == "new_email":
                setattr(new_user, "email", request_json.get('new_email'))

        setattr(new_user, "timezone", "Etc/GMT")
    # with session_scope() as session:
        session.add(new_user)
    
        logger_bp_users.info(f"- Successfully registered {new_user.email} as user id: {new_user.id}  -")

        ### Expire Website session:
        base_url_local_website = current_app.config.get('WEB_URL')
        payload_expire_website = {'ws_api_password': current_app.config.get('WS_API_PASSWORD')}
        response_expire_website = requests.get(base_url_local_website + '/expire_session', json=payload_expire_website)

        if request_json.get('new_email') != "nrodrig1@gmail.com":
            send_confirm_email(request_json.get('new_email'))

        response_dict = {}
        response_dict["message"] = f"new user created: {request_json.get('new_email')}"
        response_dict["id"] = f"{new_user.id}"
        response_dict["username"] = f"{new_user.username}"
        response_dict["alert_title"] = f"Success!"
        response_dict["alert_message"] = f""
        logger_bp_users.info(f"- Successfully registered response_dict: {response_dict}  -")
        return jsonify(response_dict)

        
# this get's sent at login
@bp_users.route('/send_data_source_objects', methods=['POST'])
@token_required
def send_data_source_objects(current_user):
    logger_bp_users.info(f"- accessed  send_data_source_objects endpoint-")
    
    list_data_source_objects = []

    # user_data_source_json_file_name = f"Dashboard-user_id{current_user.id}.json"
    user_data_source_json_file_name = f"data_source_list_for_user_{current_user.id:04}.json"
    json_data_path_and_name = os.path.join(current_app.config.get('DATA_SOURCE_FILES_DIR'), user_data_source_json_file_name)
    logger_bp_users.info(f"- Dashboard table object file name and path: {json_data_path_and_name} -")
    try:
        if os.path.exists(json_data_path_and_name):
            with open(json_data_path_and_name,'r') as data_source_json_file:
                list_data_source_objects = json.load(data_source_json_file)
                # list_data_source_objects.append(dashboard_table_object)
        else:
            logger_bp_users.info(f"File not found: {json_data_path_and_name}")
            #get user's oura record count
            # keys to data_source_object_oura must match WSiOS DataSourceObject
            # data_source_object_oura={}
            # data_source_object_oura['name']="Oura Ring"
            # with session_scope() as session:
            #   record_count_oura = session.query(OuraSleepDescriptions).filter_by(user_id=current_user.id).all()
            # data_source_object_oura['recordCount']="{:,}".format(len(record_count_oura))
            # list_data_source_objects.append(data_source_object_oura)

            #get user's apple health record count
            # keys to data_source_object_apple_health must match WSiOS DataSourceObject
            data_source_object_apple_health={}
            data_source_object_apple_health['name']="Apple Health Data"
            with session_scope() as session:
                record_count_apple_health = session.query(AppleHealthQuantityCategory).filter_by(user_id=current_user.id).all()
            data_source_object_apple_health['recordCount']="{:,}".format(len(record_count_apple_health))
            # apple_health_record_count, earliest_date_str = get_apple_health_count_date(current_user.id)
            # data_source_object_apple_health['recordCount'] = apple_health_record_count
            # data_source_object_apple_health['earliestRecordDate'] = earliest_date_str
            list_data_source_objects.append(data_source_object_apple_health)
    
        logger_bp_users.info(f"- Returning dashboard_table_object list: {list_data_source_objects} -")
        logger_bp_users.info(f"- END send_data_source_objects -")
        return jsonify(list_data_source_objects)

    except Exception as e:
        logger_bp_users.error(f"An error occurred in send_data_source_objects)")
        logger_bp_users.info(f"{type(e).__name__}: {e}")
        logger_bp_users.info(f"- END send_data_source_objects -")
        return jsonify({"error": "An unexpected error occurred"}), 500


@bp_users.route('/send_dashboard_table_objects', methods=['POST'])
@token_required
def send_dashboard_table_objects(current_user):
    logger_bp_users.info(f"- accessed  send_dashboard_table_objects endpoint-")
    
    # response_list = []
    # dashboard_table_object = {}

    # user_dashboard_json_file_name = f"Dashboard-user_id{current_user.id}.json"
    # user_sleep_dash_json_file_name = f"dt_sleep01_{current_user.id:04}.json"
    user_data_table_array_json_file_name = f"data_table_objects_array_{current_user.id:04}.json"
    # json_data_path_and_name = os.path.join(current_app.config.get('DASHBOARD_FILES_DIR'), user_sleep_dash_json_file_name)
    json_data_path_and_name = os.path.join(current_app.config.get('DASHBOARD_FILES_DIR'), user_data_table_array_json_file_name)
    logger_bp_users.info(f"- Dashboard table object file name and path: {json_data_path_and_name} -")
    try:
        with open(json_data_path_and_name,'r') as dashboard_json_file:
            dashboard_table_object_array = json.load(dashboard_json_file)
            # response_list.append(dashboard_table_object)
    
        logger_bp_users.info(f"- Returning dashboard_table_object list: {dashboard_table_object_array} -")
        logger_bp_users.info(f"- END send_dashboard_table_objects -")
        return jsonify(dashboard_table_object_array)
    except FileNotFoundError:
        error_message = f"File not found: {json_data_path_and_name}"
        logger_bp_users.error(error_message)
        logger_bp_users.info(f"- END send_dashboard_table_objects -")
        return jsonify({"error": error_message}), 404

    except Exception as e:
        logger_bp_users.info(f"{type(e).__name__}: {e}")
        logger_bp_users.info(f"- END send_dashboard_table_objects -")
        return jsonify({"error": "An unexpected error occurred"}), 500

# this get's sent at login
@bp_users.route('/delete_user', methods=['POST'])
@token_required
def delete_user(current_user):
    logger_bp_users.info(f"- accessed  delete_user endpoint-")

    deleted_records = 0

    # delete_apple_health = delete_user_from_table(current_user, AppleHealthQuantityCategory)
    delete_apple_health_qty_cat = delete_user_from_table(current_user, AppleHealthQuantityCategory)
    if delete_apple_health_qty_cat[1]:
        logger_bp_users.info(f"- Error trying to delete AppleHealthQuantityCategory for user {current_user.id}, error: {delete_apple_health_qty_cat[1]} -")
        response_message = f"- Error trying to delete AppleHealthQuantityCategory for user {current_user.id}, error: {delete_apple_health_qty_cat[1]}"
        return make_response(jsonify({"error":response_message}), 500)
    
    deleted_records = delete_apple_health_qty_cat[0]

    delete_apple_health_workouts = delete_user_from_table(current_user, AppleHealthWorkout)
    if delete_apple_health_workouts[1]:
        logger_bp_users.info(f"- Error trying to delete AppleHealthQuantityCategory for user {current_user.id}, error: {delete_apple_health_workouts[1]} -")
        response_message = f"- Error trying to delete AppleHealthQuantityCategory for user {current_user.id}, error: {delete_apple_health_workouts[1]}"
        return make_response(jsonify({"error":response_message}), 500)
    
    deleted_records = delete_apple_health_workouts[0]

    delete_oura_sleep_descriptions = delete_user_from_table(current_user, OuraSleepDescriptions)
    if delete_oura_sleep_descriptions[1]:
        logger_bp_users.info(f"- Error trying to delete OuraSleepDescriptions for user {current_user.id}, error: {delete_oura_sleep_descriptions[1]} -")
        response_message = f"Error trying to delete OuraSleepDescriptions for user {current_user.id}, error: {delete_oura_sleep_descriptions[1]} "
        return make_response(jsonify({"error":response_message}), 500)

    deleted_records += delete_oura_sleep_descriptions[0]

    delete_oura_token = delete_user_from_table(current_user, OuraToken)
    if delete_oura_token[1]:
        logger_bp_users.info(f"- Error trying to delete OuraToken for user {current_user.id}, error: {delete_oura_token[1]} -")
        response_message = f"Error trying to delete OuraToken for user {current_user.id}, error: {delete_oura_token[1]} "
        return make_response(jsonify({"error":response_message}), 500)

    deleted_records += delete_oura_token[0]

    delete_user_location_day = delete_user_from_table(current_user, UserLocationDay)
    if delete_oura_token[1]:
        logger_bp_users.info(f"- Error trying to delete UserLocationDay for user {current_user.id}, error: {delete_oura_token[1]} -")
        response_message = f"Error trying to delete UserLocationDay for user {current_user.id}, error: {delete_oura_token[1]} "
        return make_response(jsonify({"error":response_message}), 500)

    deleted_records += delete_user_location_day[0]

    # delete: dataframe pickle, data source json, and dashboard json
    delete_user_data_files(current_user)

    # delete user
    delete_user_from_users_table = delete_user_from_table(current_user, Users)
    if delete_user_from_users_table[1]:
        logger_bp_users.info(f"- Error trying to delete Users for user {current_user.id}, error: {delete_user_from_users_table[1]} -")
        response_message = f"Error trying to delete Users for user {current_user.id}, error: {delete_user_from_users_table[1]} "
        return make_response(jsonify({"error":response_message}), 500)

    deleted_records += delete_user_from_users_table[0]

    ### Expire Website session:
    base_url_local_website = current_app.config.get('WEB_URL')
    payload_expire_website = {'ws_api_password': current_app.config.get('WS_API_PASSWORD')}
    response_expire_website = requests.get(base_url_local_website + '/expire_session', json=payload_expire_website)

    response_dict = {}
    response_dict['message'] = "Successful deletion."
    response_dict['count_deleted_rows'] = "{:,}".format(deleted_records)

    logger_bp_users.info(f"- response_dict: {response_dict} -")
    return jsonify(response_dict)


# @bp_users.route('/reset_password', methods = ["GET", "POST"])
@bp_users.route('/get_reset_password_token', methods = ["GET", "POST"])
def get_reset_password_token():

    logger_bp_users.info(f"- accessed: get_reset_password_token endpoint pinged -")
    logger_bp_users.info(request.json)
    #############################################################################################
    ## In case of emergency, ACTIVATE_TECHNICAL_DIFFICULTIES_ALERT prevents users from logging in
    if current_app.config.get('ACTIVATE_TECHNICAL_DIFFICULTIES_ALERT'):
        response_dict = response_dict_tech_difficulties_alert(response_dict = {})
        return jsonify(response_dict)
    #############################################################################################
    

    try:
        request_json = request.json
        logger_bp_users.info(f"request_json: {request_json}")
    except Exception as e:
        logger_bp_users.info(f"failed to read json")
        logger_bp_users.info(f"{type(e).__name__}: {e}")
        response = jsonify({"error": str(e)})
        return make_response(response, 400)


    if current_app.config.get('WS_API_PASSWORD') != request_json.get('ws_api_password'):
        response_dict = {}
        response_dict['alert_title'] = ""
        response_dict['alert_message'] = f"Requests not from What Sticks Platform applications will not be supported."

        return jsonify(response_dict)

    # if request.method == 'POST':
    # formDict = request.form.to_dict()
    email = request_json.get('email')
    with session_scope() as session:
        user = session.query(Users).filter_by(email=email).first()
        logger_bp_users.info(f"- user: {user} -")
        if user:
            logger_bp_users.info('Email reaquested to reset: ', email)
            send_reset_email(user)
            response_dict = {}
            response_dict['alert_title'] = "Success"
            response_dict['alert_message'] = f"Email sent to {email} with reset information"
            return jsonify(response_dict)

        else:
            response_dict = {}
            response_dict['alert_title'] = "Success"
            response_dict['alert_message'] = f" {email} has no account with What Sticks"

            return jsonify(response_dict)


@bp_users.route('/reset_password', methods = ["GET"])
@token_required
def reset_password(current_user):
    logger_bp_users.info(f"- accessed: reset_password with token")
    try:
        request_json = request.json
        logger_bp_users.info(f"successfully read request_json: {request_json}")
    except Exception as e:
        logger_bp_users.info(f"failed to read json")
        logger_bp_users.info(f"{type(e).__name__}: {e}")
        response = jsonify({"error": str(e)})
        return make_response(response, 400)

    if current_user:
        try:
            with session_scope() as session:
                # Assuming current_user is correctly tied to the session, directly update its properties
                hash_pw = bcrypt.hashpw(request_json.get('password_text').encode(), salt)
                current_user.password = hash_pw
                session.merge(current_user)  # This may be necessary if current_user is detached
                # session.commit() is not needed here since session_scope() handles it
            response_dict = {
                'alert_title': "Success",
                'alert_message': f"{current_user.username}'s password has been reset"
            }
        except Exception as e:
            logger_bp_users.info(f"Error resetting password: {type(e).__name__}: {e}")
            response_dict = {
                'alert_title': "Failed",
                'alert_message': "Password reset failed due to an error."
            }
        return jsonify(response_dict)
    else:
        response_dict = {}
        response_dict['alert_title'] = "Failed"
        response_dict['alert_message'] = f" {current_user.username}'s password has NOT been reset"

        return jsonify(response_dict)


@bp_users.route('/update_user_location_with_lat_lon', methods=["POST"])
@token_required
def update_user_location_with_lat_lon(current_user):
    logger_bp_users.info(f"- update_user_location_with_lat_lon endpoint pinged -")
    try:
        request_json = request.json
        logger_bp_users.info(f"request_json: {request_json}")
    except Exception as e:
        logger_bp_users.info(f"failed to read json in update_user_location_with_lat_lon")
        logger_bp_users.info(f"{type(e).__name__}: {e}")
        response = jsonify({"error": str(e)})
        return make_response(response, 400)

    #update permission
    location_permission = request_json.get('location_permission') == "True"
    location_reoccuring_permission = request_json.get('location_reoccuring_permission') == "True"
    with session_scope() as session:
        current_user.location_permission=location_permission
        current_user.location_reoccuring_permission=location_reoccuring_permission
        # No explicit session.merge(current_user) needed here

    response_dict = {}

    #if permission granted:
    # this is conveservative, perhaps use location_permission?
    if not location_reoccuring_permission:
        response_dict["message"] = f"Removed user location tracking"
        return jsonify(response_dict)

    if 'latitude' not in request_json.keys():
        print("- no latitude but reoccuring set to True")
        response_dict["message"] = f"Updated status to reoccuring data collection"
        return jsonify(response_dict)

    # Add to User's table
    latitude = float(request_json.get('latitude'))
    longitude = float(request_json.get('longitude'))

    timezone_str = convert_lat_lon_to_timezone_string(latitude, longitude)
    # No explicit session.merge(current_user) needed here
    with session_scope() as session:
        current_user.lat = latitude
        current_user.lon = longitude
        current_user.timezone = timezone_str
        # sess .commit() <-- no longer needed because session_scope() commits the changes.
        # No explicit session.merge(current_user) needed here

    # Get the current datetime
    current_datetime = datetime.now()

    # Convert the datetime to a string in the specified format
    formatted_datetime = current_datetime.strftime('%Y%m%d-%H%M')

    # Add to UserLocationDay (and Location, if necessary)
    location_id = add_user_loc_day_process(current_user.id,latitude, longitude, formatted_datetime)
    with session_scope() as session:
        user_location = session.get(Locations, location_id)
    response_dict["message"] = f"Updated user location in UserLocDay Table with {user_location.city}, {user_location.country}"

    return jsonify(response_dict)



@bp_users.route('/update_user_location_with_user_location_json', methods=["POST"])
@token_required
def update_user_location_with_user_location_json(current_user):
    logger_bp_users.info(f"- update_user_location_with_user_location_json endpoint pinged -")
    try:
        request_json = request.json
        logger_bp_users.info(f"request_json: {request_json}")
    except Exception as e:
        logger_bp_users.info(f"failed to read json in update_user_location_with_user_location_json")
        logger_bp_users.info(f"{type(e).__name__}: {e}")
        response = jsonify({"error": str(e)})
        return make_response(response, 400)
    with session_scope() as session:
        user_location_list = request_json.get('user_location')
        timestamp_str = request_json.get('timestamp_utc')
        user_loction_filename = f"user_location-user_id{current_user.id}.json"
        json_data_path_and_name = os.path.join(current_app.config.get('USER_LOCATION_JSON'),user_loction_filename)

        with open(json_data_path_and_name, 'w') as file:
            json.dump(user_location_list, file, indent=4)
        
        try:
            for location in user_location_list:
                dateTimeUtc = location.get('dateTimeUtc')
                latitude = location.get('latitude')
                longitude = location.get('longitude')
                add_user_loc_day_process(current_user.id,latitude, longitude, dateTimeUtc)

            logger_bp_users.info(f"- successfully added user_location.json data to UserLocationDay -")

            response_dict = {}
            response_dict['alert_title'] = "Success!"# < -- This is expected response for WSiOS to delete old user_locations.json
            response_dict['alert_message'] = ""

            return jsonify(response_dict)
        except Exception as e:
            logger_bp_users.info(f"- Error trying to add user_location.json from iOS -")
            logger_bp_users.info(f"- {type(e).__name__}: {e} -")

            response_dict = {}
            response_dict['alert_title'] = "Failed"
            response_dict['alert_message'] = "Something went wrong adding user's location to database."

            return jsonify(response_dict)


# Expire session to refresh app with new data from database
@bp_users.route('/expire_session', methods = ['GET'])
def expire_session():
    logger_bp_users.info("- accessed expire_session -")

    request_json = request.json
    ws_api_password = request_json.get('ws_api_password')

    if ws_api_password == current_app.config.get('WS_API_PASSWORD'):
        with session_scope() as session:
            # Expire session so new data will take into effect when user logs in again
            session.expire_all()
        logger_bp_users.info("- Successfully expired session -")
        # return redirect(url_for(request.referrer))
        response_dict = {}
        response_dict['alert_title'] = "Success"
        response_dict['alert_message'] = f"Successfully expired session"

        return jsonify(response_dict)


