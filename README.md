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
| MC | 5010 | Generates medical certificates via Amazon S3 |
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

### `.env` files

Each service that needs configuration ships with a `.env.example` template. Copy it and fill in your own credentials before starting:

```bash
cp backend/<service-name>/.env.example backend/<service-name>/.env
```

The following services each have a `.env.example`:

| Service | Credentials required |
|---------|----------------------|
| `amazon-s3-wrapper` | AWS access key, secret, region, bucket name |
| `openai-wrapper` | OpenAI API key |
| `twilio-wrapper` | Twilio Account SID, Auth Token, API Key SID & Secret |
| `payment_wrapper` | Stripe secret key, webhook secret |
| `payment_atomic` | Stripe secret key, frontend URL |
| `notification_wrapper` | SMU Lab Notification API URL & key, RabbitMQ URL |
| `telegram_wrapper` | Telegram Bot token, RabbitMQ URL |
| `distance-matrix-wrapper` | Google Maps API key |
| `process_payment_composite` | Internal service URLs, OutSystems base URL, RabbitMQ URL |
| `complete-consultation-composite` | Internal service URLs, OutSystems base URL |
| `start-consultation-composite` | Internal service URLs |
| `book-appointment` | Internal service URLs, RabbitMQ URL, Redis URL |
| `consultation-service` | Firebase credential paths |
| `inventory` | S3 wrapper URL, folder/subfolder paths |
| `mc-service` | S3 wrapper URL, folder/subfolder paths |

### Firebase service account keys

Several atomic services connect directly to Firebase Firestore. Place the appropriate JSON key file in the service directory before running:

| Service | Key file expected |
|---------|-------------------|
| `appointment-service` | `serviceAccountKey.json` |
| `doctor-service` | `serviceAccountKey.json` |
| `patient-service` | `serviceAccountKey.json` |
| `rider-service` | `serviceAccountKey.json` |
| `delivery-service` | `serviceAccountKey.json` |
| `consultation-service` | `serviceAccountKey.json` **and** `firebase_credentials.json` |

Download these from the [Firebase Console](https://console.firebase.google.com/) under **Project Settings → Service Accounts → Generate new private key** and rename the downloaded file accordingly.

### Where to obtain external credentials

| Credential | Where to get it |
|------------|----------------|
| OpenAI API key | [platform.openai.com](https://platform.openai.com/) → API keys |
| Stripe secret key | [dashboard.stripe.com](https://dashboard.stripe.com/) → Developers → API keys |
| Twilio SID / Auth Token | [console.twilio.com](https://console.twilio.com/) → Account Info |
| Telegram Bot token | Talk to [@BotFather](https://t.me/BotFather) on Telegram |
| Google Maps API key | [console.cloud.google.com](https://console.cloud.google.com/) → APIs & Services → Credentials |
| SMU Lab Utilities Amazon S3 & Notification API | [smuedu-dev.outsystemsenterprise.com/SMULabUtilities](https://smuedu-dev.outsystemsenterprise.com/SMULabUtilities/) → API Keys |

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

The website will be available at [http://localhost:8080](http://localhost:8080).

To stop all services:

```bash
docker compose down
```

### Running services across multiple machines

See [`Machine Split Files/SETUP.md`](Machine%20Split%20Files/SETUP.md) for instructions on splitting services across multiple devices on the same network.

## Contributors

**ESD Section G11 Team 2**

- Cheung Kele Paolo
- Lichelle Weasley
- Jeniffer Joyce
- Lee Hong Yi
- Seann Khoo
