from flask import Blueprint
from flask import request, jsonify, make_response, current_app
from ws_models import sess, Users, OuraToken, OuraSleepDescriptions, AppleHealhKit
from werkzeug.security import generate_password_hash, check_password_hash #password hashing
import bcrypt
import datetime
from itsdangerous.url_safe import URLSafeTimedSerializer#new 2023
import logging
import os
from logging.handlers import RotatingFileHandler
import json
import socket
from app_package.utilsDecorators import token_required


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

    logger_bp_users.info(f"{current_app.config.get('WS_API_PASSWORD')}")

    hostname = socket.gethostname()

    return jsonify(f"Yes! We're up! in the {hostname} machine")


@bp_users.route('/login',methods=['POST'])
def login():
    logger_bp_users.info(f"- login endpoint pinged -")
    logger_bp_users.info(f"All Headers: {request.headers}")

    auth = request.authorization
    logger_bp_users.info(f"- auth.username: {auth.username} -")

    if not auth or not auth.username or not auth.password:
        return make_response('Could not verify', 401)

    user = sess.query(Users).filter_by(email= auth.username).first()

    if not user:
        return make_response('Could not verify - user not found', 401)

    logger_bp_users.info(f"- checking password -")
    logger_bp_users.info(f"- password: {auth.password.encode()} -")
    logger_bp_users.info(f"- password: {bcrypt.checkpw(auth.password.encode(), user.password)} -")


    if auth.password:
        if bcrypt.checkpw(auth.password.encode(), user.password):
            serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])

            user_object_for_swift_app = {}
            user_object_for_swift_app['id'] = str(user.id)
            user_object_for_swift_app['email'] = user.email
            user_object_for_swift_app['username'] = user.username
            user_object_for_swift_app['password'] = "test"
            user_object_for_swift_app['token'] = serializer.dumps({'user_id': user.id})
            oura_token_obj = sess.query(OuraToken).filter_by(user_id=user.id).first()
            if oura_token_obj and oura_token_obj.token is not None:
                user_object_for_swift_app['oura_token'] = oura_token_obj.token

            user_object_for_swift_app['admin'] = True

            # oura_health_stuct = {}
            # oura_health_stuct['name']='Oura'
            # user_oura_sessions = sess.query(OuraSleepDescriptions).filter_by(user_id=user.id).all()
            # if user_oura_sessions is not None:
            #     oura_health_stuct['recordCount']="{:,}".format(len(user_oura_sessions))
            # user_object_for_swift_app['ouraHealthStruct']=oura_health_stuct

            # apple_health_struct = {}
            # apple_health_struct['name']="Apple Health"
            # apple_health_records = sess.query(AppleHealhKit).filter_by(user_id=user.id).all()
            # if apple_health_records is not None:
            #     apple_health_struct['recordCount']="{:,}".format(len(apple_health_records))
            # user_object_for_swift_app['appleHealthStruct']=apple_health_struct


            logger_bp_users.info(f"- user_object_for_swift_app: {user_object_for_swift_app} -")
            return jsonify(user_object_for_swift_app)

    return make_response('Could not verify', 401)
    # else:
    #     return make_response('Could note verify sender', 401)


@bp_users.route('/register', methods=['POST'])
def register():
    logger_bp_users.info(f"- register endpoint pinged -")
    # ws_api_password = request.json.get('WS_API_PASSWORD')
    logger_bp_users.info(request.json)
    # if current_app.config.get('WS_API_PASSWORD') == ws_api_password:
    try:
        request_json = request.json
        logger_bp_users.info(f"request_json: {request_json}")
    except Exception as e:
        logger_bp_users.info(f"failed to read json, error: {e}")
        response = jsonify({"error": str(e)})
        return make_response(response, 400)

    if request_json.get('new_email') in ("", None) or request_json.get('new_password') in ("" , None):
        return jsonify({"message": f"User must have email and password"})

    user_exists = sess.query(Users).filter_by(email= request_json.get('new_email')).first()

    if user_exists:
        return jsonify({"message": f"User already exists"})

    hash_pw = bcrypt.hashpw(request_json.get('new_password').encode(), salt)
    new_user = Users()

    for key, value in request_json.items():
        if key == "new_password":
            setattr(new_user, "password", hash_pw)
        elif key == "new_email":
            setattr(new_user, "email", request_json.get('new_email'))

    sess.add(new_user)
    sess.commit()

    response_dict = {}
    response_dict["message"] = f"new user created: {request_json.get('new_email')}"
    response_dict["id"] = f"{new_user.id}"
    response_dict["username"] = f"{new_user.username}"


    return jsonify(response_dict)

        

@bp_users.route('/send_dashboard_health_data_objects', methods=['POST'])
@token_required
def send_dashboard_health_data_objects(current_user):
    logger_bp_users.info(f"- accessed  send_dashboard_health_data_objects endpoint-")
    response_list = []


    #get user's oura record count
    dashboard_health_data_object_oura={}
    dashboard_health_data_object_oura['name']="Oura Ring"
    record_count_oura = sess.query(OuraSleepDescriptions).filter_by(user_id=current_user.id).all()
    dashboard_health_data_object_oura['recordCount']="{:,}".format(len(record_count_oura))
    response_list.append(dashboard_health_data_object_oura)

    #get user's apple health record count
    dashboard_health_data_object_apple_health={}
    dashboard_health_data_object_apple_health['name']="Apple Health Data"
    record_count_apple_health = sess.query(AppleHealhKit).filter_by(user_id=current_user.id).all()
    dashboard_health_data_object_apple_health['recordCount']="{:,}".format(len(record_count_apple_health))
    response_list.append(dashboard_health_data_object_apple_health)


    # try:
    #     count_deleted_rows = sess.query(AppleHealhKit).filter_by(user_id = 1).delete()
    #     sess.commit()
    #     response_message = f"successfully deleted {count_deleted_rows} records"
    # except Exception as e:
    #     session.rollback()
    #     logger_bp_users.info(f"failed to delete data, error: {e}")
    #     response_message = f"failed to delete, error {e} "
    #     # response = jsonify({"error": str(e)})
    #     return make_response(jsonify({"error":response_message}), 500)

    # response_dict['message'] = response_message
    # response_dict['count_deleted_rows'] = "{:,}".format(count_deleted_rows)
    # response_dict['count_of_entries'] = "0"

    logger_bp_users.info(f"- response_list: {response_list} -")
    return jsonify(response_list)



