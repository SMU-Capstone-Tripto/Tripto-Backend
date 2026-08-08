import firebase_admin
from firebase_admin import credentials, messaging
from app.core.config import settings
import os
import json

_firebase_initialized = False

def initialize_firebase():
    global _firebase_initialized

    if _firebase_initialized:
        return

    try:
        # Firebase 서비스 계정 키를 환경변수나 파일에서 로드
        firebase_creds = os.getenv("FIREBASE_CREDENTIALS")

        if firebase_creds:
            # 환경변수에서 JSON 문자열로 로드
            cred_dict = json.loads(firebase_creds)
            cred = credentials.Certificate(cred_dict)
        else:
            # 파일에서 로드 (개발 환경)
            cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "./firebase-credentials.json")
            if os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)
            else:
                print("Warning: Firebase credentials not found. FCM notifications will not work.")
                return

        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        print("Firebase initialized successfully")

    except Exception as e:
        print(f"Failed to initialize Firebase: {e}")


def get_firebase_messaging():
    if not _firebase_initialized:
        initialize_firebase()

    return messaging if _firebase_initialized else None
