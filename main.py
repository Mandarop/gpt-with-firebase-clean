import os
import openai
import uuid
import firebase_admin
from firebase_admin import credentials, firestore, initialize_app, auth
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
import pytz

# Load environment variables from .env file
load_dotenv()

# Fetch API Key and Firebase credentials path from environment variables
API_KEY = os.getenv("OPENAI_API_KEY")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")

# Ensure both API key and Firebase credentials are provided
if not API_KEY or not FIREBASE_CREDENTIALS_PATH:
    raise ValueError("Missing required environment variables: OPENAI_API_KEY or FIREBASE_CREDENTIALS_PATH")

# Set OpenAI API key
openai.api_key = API_KEY

# Initialize Flask App
app = Flask(__name__)
CORS(app)

# Initialize Firebase with the provided credentials
cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
initialize_app(cred)
db = firestore.client()

# Time Management: Functions to manage timestamps and current time in IST
def get_ist_time():
    return datetime.now(pytz.timezone("Asia/Kolkata"))

def get_date_str():
    return get_ist_time().strftime('%Y-%m-%d')

# System Prompt for the assistant
system_prompt = """
तुम्ही 'मनोदर्पण' मानसिक आरोग्य सहाय्यक आहात. तुमचा उद्देश वापरकर्त्यांची मानसिक स्थिती समजून त्यांना आधार देणे आहे. तुमचे उत्तर नेहमी मराठीत असले पाहिजे.

You are a friendly, compassionate mental health assistant designed to support users after they complete tests for depression and anxiety. Your primary tasks are to monitor their emotional well-being, provide thoughtful feedback, and offer helpful suggestions when needed.

Your Responsibilities:
1️ Daily Check-ins
2️ Emotional Analysis
3️ Providing Support
4️ Encouraging Positive Habits
5️ Recognizing Urgent Situations

Also give all the responses in Marathi language.
"""

# Function to get response from OpenAI's GPT model
def get_response(conversation):
    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=conversation,
        temperature=0.8
    )
    return response["choices"][0]["message"]["content"]

# Store the user's message in Firestore under user_sessions subcollection per user
def save_user_session(uid, session_id, user_message):
    date_str = get_date_str()
    user_session_ref = db.collection("users").document(uid).collection("user_sessions").document(date_str)
    user_session_ref.set({
        "date": date_str,
        "user_messages": firestore.ArrayUnion([{
            "session_id": session_id,
            "user_message": user_message,
            "timestamp": get_ist_time().isoformat()
        }])
    }, merge=True)

# Store the entire chat (user and bot messages) in Firestore under chat_history per user
def save_chat_history(uid, session_id, user_message, bot_message):
    date_str = get_date_str()
    chat_history_ref = db.collection("users").document(uid).collection("chat_history").document(date_str)
    chat_history_ref.set({
        "date": date_str,
        "chats": firestore.ArrayUnion([{
            "session_id": session_id,
            "user_message": user_message,
            "bot_message": bot_message,
            "timestamp": get_ist_time().isoformat()
        }])
    }, merge=True)

# Retrieve session chat history for a particular user and session
def get_session_history(uid, session_id):
    date_str = get_date_str()
    chat_history_ref = db.collection("users").document(uid).collection("chat_history").document(date_str)
    chat_doc = chat_history_ref.get()

    history = [{"role": "system", "content": system_prompt}]
    if chat_doc.exists:
        chats = chat_doc.to_dict().get("chats", [])
        for chat in chats:
            if chat["session_id"] == session_id:
                history.append({"role": "user", "content": chat["user_message"]})
                history.append({"role": "assistant", "content": chat["bot_message"]})
    return history

# Middleware to verify Firebase ID token and extract UID
# For quick local testing: if X-UID header is present, use it directly
def verify_token(request):
    # Allow override for local testing
    test_uid = request.headers.get('X-UID')
    if test_uid:
        return test_uid

    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None
    id_token = auth_header.split('Bearer ')[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token['uid']
    except Exception:
        return None

# POST /chat Endpoint to receive user message, get bot response and store the session
@app.route('/chat', methods=['POST'])
def chat():
    uid = verify_token(request)
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    session_id = data.get('session_id') or str(uuid.uuid4())  # If session_id not provided, generate a new one
    user_message = data.get('message')

    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    # Save user message and chat history under this UID
    save_user_session(uid, session_id, user_message)
    conversation = get_session_history(uid, session_id)
    conversation.append({"role": "user", "content": user_message})
    bot_response = get_response(conversation)
    save_chat_history(uid, session_id, user_message, bot_response)

    return jsonify({"response": bot_response, "session_id": session_id})

# GET /get_chat_history Endpoint to fetch chat history for a specific date for authenticated user
@app.route('/get_chat_history', methods=['GET'])
def get_chat_history():
    uid = verify_token(request)
    if not uid:
        return jsonify({"error": "Unauthorized"}), 401

    date_str = request.args.get('date') or get_date_str()
    chat_history_ref = db.collection("users").document(uid).collection("chat_history").document(date_str)
    chat_doc = chat_history_ref.get()

    history = []
    if chat_doc.exists:
        chats = chat_doc.to_dict().get("chats", [])
        history = [
            {"sender": "user", "text": chat["user_message"]} if chat.get("user_message") else {"sender": "bot", "text": chat["bot_message"]}
            for chat in chats
        ]

    return jsonify({"history": history})

# Run the Flask app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
