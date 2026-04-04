<div align="center" display="inline">
  <img src="logo.svg" width="72" height="72" alt="MediConnect logo"/>
  <h1>MediConnect</h1>
</div>

MediConnect is an online teleconsultation platform that connects patients with doctors across multiple medical specialisations. Patients can book appointments, attend video consultations, receive prescriptions, and have their medications delivered to their doorstep - all through a single web application.

## User Roles

| Role | Description |
|------|-------------|
| **Patient** | Book appointments, attend video consultations, view prescriptions, track deliveries, make payments |
| **Doctor** | Manage schedule and availability, conduct video consultations, issue prescriptions and MCs |
| **Delivery Rider** | Accept delivery assignments, scan QR codes to confirm delivery completion |

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
| Assign Delivery | 5002 | Finds the nearest available rider and assigns a delivery |
| Book Appointment | 5033 | Orchestrates appointment booking across patient, doctor and appointment services |
| Complete Consultation | 5014 | Finalises consultation: deducts inventory, creates order, issues MC, sends notifications |
| Complete Delivery | 5004 | Marks delivery as complete and publishes payment/notification events |
| Process Payment | 5002 | Processes Stripe payments and handles refunds |
| Start Consultation | 5013 | Initiates a Twilio video room and creates the consultation record |

### Wrapper Services

| Service | Port | Description |
|---------|------|-------------|
| Amazon S3 | 5022 | Uploads and retrieves files from AWS S3 |
| OpenAI | 5021 | Generates AI-powered consultation summaries |
| Telegram | 5012 | Sends Telegram bot notifications |
| Twilio | 5020 | Creates and manages Twilio Video rooms |
| Notification | 5011 | Routes notifications (email/SMS) via RabbitMQ and pushes SSE events |

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