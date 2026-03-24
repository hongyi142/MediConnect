import os
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def get_firebase_credentials():
    """
    Build Firebase credentials dict from environment variables.
    Returns a dict that can be used with firestore.Client(credentials=...) or saved to JSON.
    """
    return {
        "type": "service_account",
        "project_id": os.getenv("FIREBASE_PROJECT_ID"),
        "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
        "private_key": os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
        "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
        "client_id": os.getenv("FIREBASE_CLIENT_ID"),
        "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
        "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
        "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_X509_CERT_URL"),
        "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_X509_CERT_URL"),
    }


def init_firestore():
    """Initialize and return a Firestore client.
    """
    from google.cloud import firestore
    from google.oauth2 import service_account

    project_id = os.getenv("FIREBASE_PROJECT_ID")
    has_service_account = all(
        [
            os.getenv("FIREBASE_PRIVATE_KEY"),
            os.getenv("FIREBASE_CLIENT_EMAIL"),
            os.getenv("FIREBASE_PRIVATE_KEY_ID"),
        ]
    )

    if has_service_account:
        creds_dict = get_firebase_credentials()
        credentials = service_account.Credentials.from_service_account_info(creds_dict)
        return firestore.Client(project=project_id, credentials=credentials)

    # Fallback
    # auth resolved through GOOGLE_APPLICATION_CREDENTIALS or gcloud ADC.
    if not project_id:
        raise ValueError("FIREBASE_PROJECT_ID is required")
    return firestore.Client(project=project_id)


# Convenience getters for common API keys
def get_openai_api_key():
    return os.getenv("OPENAI_API_KEY")


def get_twilio_credentials():
    return {
        "account_sid": os.getenv("TWILIO_ACCOUNT_SID"),
        "auth_token": os.getenv("TWILIO_AUTH_TOKEN"),
        "api_key": os.getenv("TWILIO_API_KEY"),
        "api_secret": os.getenv("TWILIO_API_SECRET"),
    }


def get_stripe_credentials():
    return {
        "api_key": os.getenv("STRIPE_API_KEY"),
        "secret_key": os.getenv("STRIPE_SECRET_KEY"),
    }


def get_smu_credentials():
    return {
        "api_url": os.getenv("SMU_API_URL"),
        "sms_api_url": os.getenv("SMU_SMS_API_URL"),
        "x_contacts_key": os.getenv("SMU_X_CONTACTS_KEY"),
    }
