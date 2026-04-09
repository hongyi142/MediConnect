<div align="center">
  <img src="logo.svg" width="320" height="56" alt="MediConnect"/>
</div>
<br/>

MediConnect is an online teleconsultation platform that connects patients with doctors across multiple medical specialisations. Patients can book appointments, attend video consultations, receive prescriptions and have their medications delivered to their doorstep - all through a single web application.

## User Roles

| Role | Description |
|------|-------------|
| **Patient** | Book appointments, attend video consultations, view prescriptions, make payments, track deliveries |
| **Doctor** | Conduct video consultations, issue prescriptions and MCs, manage staff and inventory |
| **Delivery Rider** | Accept and deliver delivery assignments |

## Services

### Atomic Services

| Service | Port | Description |
|---------|------|-------------|
| Appointment | 5032 | Manages appointment records and status |
| Consultation | 5004 | Stores consultation notes, prescriptions and summaries |
| Delivery | 5000 | Tracks delivery orders and status |
| Doctor | 5031 | Manages doctor profiles and schedules |
| Inventory | 5005 | Manages medication stock |
| MC | 5010 | Generates medical certificates and stores in AWS S3 |
| Order | OutSystems | Manages and stores orders (prescriptions) and order items |
| Patient | 5030 | Manages patient profiles |
| Payment | 5000/5001 | Handles payment transactions (atomic + wrapper) |
| Rider | 5001 | Manages rider profiles and availability |
| Tracking | 5050 | Real-time delivery tracking via WebSocket |

### Composite Services

| Service | Port | Description |
|---------|------|-------------|
| Assign Delivery | 5002 | Finds the nearest available rider (via distance-matrix-wrapper) and assigns a delivery |
| Book Appointment | 5033 | Orchestrates appointment booking across patient, doctor and appointment services |
| Complete Consultation | 5014 | Finalises consultation: deducts inventory, creates order, issues MC, sends notifications |
| Complete Delivery | 5004 | Marks delivery as complete and publishes payment/notification events |
| Process Payment | 5002 | Processes Stripe payments and handles refunds |
| Start Consultation | 5013 | Initiates a Twilio video room and creates the consultation record |

### Wrapper Services

| Service | Port | Description |
|---------|------|-------------|
| Amazon S3 | 5022 | Uploads and retrieves files from AWS S3 |
| Distance Matrix | 5063 | Wraps Google Maps Geocoding + Distance Matrix APIs for rider-distance calculations |
| OpenAI | 5021 | Generates AI-powered consultation summaries |
| Telegram | 5012 | Sends Telegram bot notifications |
| Twilio | 5020 | Creates and manages Twilio Video rooms |
| Notification | 5011 | Routes notifications (email/SMS) via RabbitMQ and pushes SSE events |

## Environment Setup

Most services require environment variables and/or Firebase credentials.

### Setup checklist (do this before running `docker compose up`)

Complete these steps in order:

1. **Root `.env`** — copy the root template and fill in your Firebase credentials:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and replace the placeholder values with your Firebase project's config (see [Firebase Console](https://console.firebase.google.com/) → Project Settings → General → Your apps). This is required for the frontend and Kong JWT validation to work.

2. **Service `.env` files** — copy each service template and fill in credentials:
   ```bash
   cp backend/<service-name>/.env.example backend/<service-name>/.env
   ```
   See the table below for which services need credentials and what they are.

3. **Firebase service account keys** — place `serviceAccountKey.json` in each service directory listed in the Firebase keys table below.

4. **Run**:
   ```bash
   docker compose up --build
   ```

---

### `.env` files

Each service that needs configuration ships with a `.env.example` template.

| Service | Credentials required |
|---------|----------------------|
| `amazon-s3-wrapper` | SMU Lab Utilities API Key |
| `openai-wrapper` | OpenAI API Key |
| `twilio-wrapper` | Twilio Account SID, Auth Token, API Key SID & Secret |
| `payment_wrapper` | Stripe Secret Key |
| `notification_wrapper` | SMU Lab Utilities API Key |
| `telegram_wrapper` | Telegram Bot Token |
| `distance-matrix-wrapper` | Google Maps API Key |
| `process_payment_composite` | Consultation Fee Pricing (Currently set at $40) |
| `complete-consultation-composite` | Consultation Fee Pricing (Currently set at $40) |
| `inventory` | SMU Lab Utilities AWS S3 folder/subfolder paths |
| `mc-service` | SMU Lab Utilities AWS S3 folder/subfolder paths |
| `start-consultation-composite` | No external credentials — copy `.env.example` as-is |

### Firebase service account keys

Several services connect directly to Firebase Firestore. Place the appropriate JSON key file in each service directory before running:

| Service | Key file expected |
|---------|-------------------|
| `appointment-service` | `serviceAccountKey.json` |
| `doctor-service` | `serviceAccountKey.json` |
| `patient-service` | `serviceAccountKey.json` |
| `rider-service` | `serviceAccountKey.json` |
| `delivery-service` | `serviceAccountKey.json` |
| `consultation-service` | `serviceAccountKey.json` |
| `payment_atomic` | `serviceAccountKey.json` |

Download these from the [Firebase Console](https://console.firebase.google.com/) under **Project Settings → Service Accounts → Generate new private key** and rename the downloaded file accordingly.

### Where to obtain external credentials

| Credential | Where to get it |
|------------|----------------|
| OpenAI API Key | [platform.openai.com](https://platform.openai.com/) → API keys |
| Stripe Secret Key | [dashboard.stripe.com](https://dashboard.stripe.com/) → Developers → API keys |
| Twilio SID / Auth Token | [console.twilio.com](https://console.twilio.com/) → Account Info |
| Telegram Bot Token | Talk to [@BotFather](https://t.me/BotFather) on Telegram |
| Google Maps API Key | [console.cloud.google.com](https://console.cloud.google.com/) → APIs & Services → Credentials |
| SMU Lab Utilities Amazon S3 & Notification API Key | [smuedu-dev.outsystemsenterprise.com/SMULabUtilities](https://smuedu-dev.outsystemsenterprise.com/SMULabUtilities/) → API Keys |

## Main Features

- Patient appointment booking with specialisation and doctor selection
- Video consultations via Twilio Video
- AI-generated consultation summaries via OpenAI
- Prescription management with medication selection from live inventory
- Medical certificate (MC) generation and storage on AWS S3
- Medication order creation via OutSystems Order API
- Delivery assignment to nearest available rider via Google Maps Distance Matrix
- Real-time delivery tracking via WebSocket
- Stripe payment processing
- Telegram bot and SSE push notifications
- Firebase JWT validation and rate limiting at the Kong API gateway

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Jinja2 templates, Vanilla JS, Firebase JS SDK |
| Backend | Python Flask (atomic + composite microservices) |
| API Gateway | Kong (DB-less declarative mode) |
| Database | Firebase Firestore |
| Authentication | Firebase Authentication |
| Video | Twilio Video |
| Messaging | RabbitMQ (async notification events) |
| Payments | Stripe |
| Storage | AWS S3 via SMU Lab Utilities wrapper |
| AI | OpenAI GPT (consultation summaries) |
| Maps | Google Maps Distance Matrix API |
| Notifications | SMS and Email via SMU Lab Utilities wrapper, Telegram Bot, Server-Sent Events (SSE) |
| Containerisation | Docker Compose |

## Usage

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- [Python 3.11+](https://www.python.org/) (for local development only)
- [Visual Studio Code](https://code.visualstudio.com/) with the [Python extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python) (recommended)

Create a Python virtual environment for any local development:

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r backend/<service-name>/requirements.txt
```

### Running all services on one machine

From the project root:

```bash
docker compose up --build
```

To stop all services:

```bash
docker compose down
```

### Running services across multiple machines

See [`Machine Split Files/SETUP.md`](Machine%20Split%20Files/SETUP.md) for instructions on splitting services across multiple devices on the same network.

### Service URLs

After startup, the following are available:

| Service | URL |
|---------|-----|
| Frontend | http://localhost:8080 |
| Kong API Gateway | http://localhost:8000 |
| Kong Admin API | http://localhost:8001 |
| RabbitMQ Management | http://localhost:15672 (guest / guest) |

## Using the Application

### Test Accounts

Pre-created accounts are available for the doctor and rider roles:

| Role | Email | Password |
|------|-------|----------|
| Doctor | testdoctor@mediconnect.com | MediConnect |
| Rider | testrider@mediconnect.com | MediConnect |

For the patient role, sign up for a new account.

### As a Patient

1. Sign up for a patient account and complete your profile.
2. Go to **AI Symptom Checker** and describe your symptoms - the AI will suggest a relevant specialisation.
3. Go to **Book** and select a specialisation (or use the one suggested) and an available doctor.
4. Choose a time slot and confirm the booking.
5. Wait for the doctor to confirm, then join the video consultation from **Appointments**.
6. After the consultation, go to **Appointments → Past → Prescription** to view prescribed medications and download your MC.
7. Complete payment via Stripe on the **Payment** screen.
8. Track your medication delivery from **Order Status**.
9. Present the delivery QR code to the rider to acknowledge receipt of medication delivery.

### As a Doctor

Use the test account above or sign up for a new doctor account.

1. View and confirm pending appointments from the **Dashboard**.
2. Start a consultation room when the patient joins.
3. During the consultation, you may take consultation notes and try out the AI summariser feature.
4. End the consultation to prescribe medcication (order creation) and generate an MC.
5. Add or update the medication inventory.

### As a Delivery Rider

Use the test account above or sign up for a new rider account.

1. Set your status to **Available** from the **Dashboard**.
2. Accept delivery assignments that appear on the **Dashboard**.
3. Navigate to the patient's address and scan the patient's delivery QR code to mark delivery as complete.

### Stripe Test Card

Use the following card on the payment screen (Stripe test mode):

| Field | Value |
|-------|-------|
| Card number | `4242 4242 4242 4242` |
| Expiry date | Any future date |
| CVC | Any 3 digits |

## Repository Structure

```
frontend/                          - Jinja2 + Vanilla JS web application
backend/
  appointment-service/             - Appointment records and status
  consultation-service/            - Consultation notes and prescription
  doctor-service/                  - Doctor profiles and schedules
  patient-service/                 - Patient profiles
  rider-service/                   - Rider profiles and availability
  delivery-service/                - Delivery tracking
  inventory/                       - Medication stock management
  mc-service/                      - Medical certificate generation
  payment_atomic/                  - Payment transaction records
  amazon-s3-wrapper/               - AWS S3 file upload and retrieval
  twilio-wrapper/                  - Twilio Video room management
  openai-wrapper/                  - OpenAI symptom checker and consultation summary generation
  payment_wrapper/                 - Stripe payment processing
  notification_wrapper/            - RabbitMQ consumer + SSE push notifications
  telegram_wrapper/                - Telegram bot notifications
  distance-matrix-wrapper/         - Google Maps geocoding and distance calculations
  book-appointment/                - Composite: appointment booking orchestration
  start-consultation-composite/    - Composite: Twilio room creation + consultation initialisation
  complete-consultation-composite/ - Composite: prescription, MC, order
  complete-delivery-composite/     - Composite: delivery completion + payment events
  assign-delivery-composite/       - Composite: nearest rider assignment
  process_payment_composite/       - Composite: Stripe payment + refund handling
Machine Split Files/               - Docker Compose configs for multi-machine deployment
apidocs/                           - API documentation
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| All API calls return `401` | Check `FIREBASE_PROJECT_ID` is set correctly in `.env` and matches the Firebase project |
| Kong returns `502 Bad Gateway` | Restart Kong after rebuilding: `docker compose restart kong` |
| Video call fails to connect | Verify Twilio Account SID, Auth Token, API Key SID, and API Key Secret |
| MC download button does nothing | Check AWS S3 credentials and bucket/folder config in `amazon-s3-wrapper` |
| Payment screen shows an error | Ensure `STRIPE_SECRET_KEY` is a valid test key (starts with `sk_test_`) |
| Rider assignment never triggers | Verify the Google Maps API key has Distance Matrix and Geocoding APIs enabled |
| Notifications not delivered | Check RabbitMQ is running and `TELEGRAM_BOT_TOKEN` or `SMU_X_CONTACTS_KEY` is valid |
| Prescriptions/orders missing | The OutSystems Order API must be reachable; check `ORDER_SERVICE_URL` in the composite service env |
| SSE push notifications not showing | Ensure the frontend has a valid Firebase JWT; SSE exemption is configured in Kong |
| Services start but Firestore reads fail | Confirm each service has its `serviceAccountKey.json` in the correct directory |

## Assumptions

- Deployment is Singapore-based; the Distance Matrix wrapper geocodes addresses using Singapore as the default region.
- All external services (Twilio, Stripe, OpenAI, Google Maps, S3, Telegram, OutSystems) must be reachable. Most features degrade gracefully if a wrapper is unavailable, but prescription and order creation requires the OutSystems Order API.
- The Firebase project is shared across the team. All services pointing to the same Firestore instance and using the same service account key is the expected configuration.
- RabbitMQ is required for asynchronous notification events. If unavailable, notifications are silently skipped but core flows (booking, consultation, payment) still complete.

## Contributors

**ESD Section G11 Team 2**

- Cheung Kele Paolo
- Lee Hong Yi
- Lichelle Weasley
- Jeniffer Joyce
- Seann Khoo
