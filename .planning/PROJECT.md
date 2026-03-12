# MediConnect Teleconsultation Microservices

## What This Is

A suite of 7 Flask microservices powering a teleconsultation platform for MediConnect. Handles the full consultation lifecycle — starting video calls, managing consultation records, generating AI summaries, issuing medical certificates, processing medication orders, and sending notifications. Built to integrate with 5 existing teammate-built services via HTTP and RabbitMQ.

## Core Value

The start-consultation → video call → complete-consultation pipeline must work end-to-end: a patient and doctor can join a video room, the doctor can write notes, prescribe medications, issue an MC, and the system creates an order and notifies the patient.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] 7 standalone Flask microservices, each with own Dockerfile, requirements.txt, .env.example
- [ ] consultation-service: CRUD for consultation records in Firestore
- [ ] twilio-wrapper: Video room creation and access token generation
- [ ] openai-wrapper: Clinical note summarisation and medication recommendations
- [ ] mc-service: PDF medical certificate generation, upload to S3, retrieval
- [ ] notification-service: Email/SMS via SendGrid/Twilio + RabbitMQ consumer for async events
- [ ] start-consultation: Composite orchestrator (appointment → twilio → consultation)
- [ ] complete-consultation: Composite orchestrator (notes → AI summary → inventory → order → MC → RabbitMQ)
- [ ] RabbitMQ topology pre-declared via definitions.json (exchanges, queues, bindings)
- [ ] docker-compose.yml wiring all services together with health checks
- [ ] All services have /health endpoint, CORS, error handling, 10s HTTP timeouts
- [ ] Compensating transactions for inventory deduction failures

### Out of Scope

- patient-service, doctor-service, appointment-service, inventory-service, order-service — built by teammates
- Frontend/UI — separate project
- Production deployment — local Docker Compose only
- Authentication/authorization middleware — not required for this milestone
- Real-time WebSocket bridge — consumed by frontend team

## Context

- SMU Enterprise Solution Development Y2T2 project
- Building 7 services independently first; teammate services not yet available for integration
- Firebase/Firestore for consultation data (have both web config and service account key)
- Amazon S3 via SMU OutSystems API for MC storage (specific API key required)
- RabbitMQ for async event-driven notifications
- Twilio Video for teleconsultation rooms
- OpenAI (gpt-4o-mini) for clinical note summarisation
- SendGrid for email, Twilio Messages for SMS

## Constraints

- **Tech stack**: Python 3.11, Flask, Docker — mandated by project requirements
- **Firestore init**: Use google-cloud-firestore with ADC, not firebase_admin
- **S3 API**: Must use SMU OutSystems REST API with X-Contacts-Key header, not AWS SDK
- **RabbitMQ**: All topology pre-declared in definitions.json; services don't declare exchanges/queues at runtime
- **Inter-service calls**: 10-second timeout, return 503 on failure
- **RabbitMQ publishing**: Must never crash the caller (fire-and-forget with error logging)
- **Deployment**: Local Docker Compose only

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| google-cloud-firestore over firebase_admin | Project spec requirement, ADC-based auth | — Pending |
| WeasyPrint for MC PDF generation | Generates PDF from inline HTML, no external template files | — Pending |
| rmq_helper.py copied into each service | Docker containers must be self-contained | — Pending |
| Fanout exchanges for all RabbitMQ routing | Multiple consumers per event (notification + UI) | — Pending |
| gpt-4o-mini for AI calls | Cost-effective for structured summarisation | — Pending |

---
*Last updated: 2026-03-12 after initialization*
