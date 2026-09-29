"""Hand-written core documents for the fictional company "Northwind Logistics".

All people, places and figures are invented. Each entry:
(slug, collection, fmt, title, modified_at, page_per_section, sections)
where sections = [(heading, [paragraph, ...]), ...].
"""

CORE: list[tuple[str, str, str, str, str, bool, list[tuple[str, list[str]]]]] = [
    (
        "hr-leave-policy", "company", "pdf", "Leave Policy", "2025-03-01", True,
        [
            ("Purpose and scope", [
                "This policy explains how leave works at Northwind Logistics. It applies to all permanent employees in every depot and office.",
                "Contractors and agency staff follow the terms of their own agreements. Questions about this policy can be sent to the People Team.",
            ]),
            ("Eligibility", [
                "Full-time employees become eligible for paid annual leave after completing a 3-month probation period. Leave accrues from the first day of employment but cannot be taken during probation without manager approval.",
                "Part-time employees accrue leave pro rata based on their contracted weekly hours.",
            ]),
            ("Annual leave entitlement", [
                "Every full-time employee receives 24 working days of paid annual leave per leave year, in addition to public holidays. The leave year runs from 1 January to 31 December.",
                "Requests for more than 5 consecutive working days must be submitted at least 4 weeks in advance. Managers respond to requests within 3 working days.",
            ]),
            ("Carry forward", [
                "Employees may carry forward a maximum of 10 days of unused annual leave into the next leave year. Any days above this limit are forfeited on 31 December.",
                "Carried forward days must be used by 31 March of the following year. Days not used by that date are lost and are not paid out.",
            ]),
            ("Sick leave", [
                "Employees receive 12 paid sick days per leave year. A medical certificate is required after 3 consecutive days of absence.",
                "Employees must inform their line manager before the start of their shift on the first day of sickness.",
            ]),
            ("Parental leave", [
                "The primary carer receives 16 weeks of parental leave on full pay. The secondary carer receives 4 weeks on full pay.",
                "Parental leave can be taken in one block or in two blocks within 12 months of the birth or adoption.",
            ]),
        ],
    ),
    (
        "expense-policy-v1", "company", "pdf", "Expense Policy (Version 1)", "2023-01-15", False,
        [
            ("Overview", [
                "This is version 1 of the expense policy. It sets the limits for business expenses claimed by employees of Northwind Logistics.",
            ]),
            ("Meal allowance", [
                "The daily meal allowance during business travel is 40 euros. Alcohol is not reimbursed.",
                "Receipts are required for every claim above 15 euros.",
            ]),
            ("Approval limits", [
                "Team leads can approve expenses up to 500 euros. Anything above 500 euros requires approval from a department head.",
                "Claims must be submitted within 60 days of the expense date.",
            ]),
        ],
    ),
    (
        "expense-policy-v2", "company", "pdf", "Expense Policy (Version 2)", "2025-06-01", False,
        [
            ("Overview", [
                "This is version 2 of the expense policy and it replaces version 1 from 1 June 2025. It sets the limits for business expenses claimed by employees of Northwind Logistics.",
            ]),
            ("Meal allowance", [
                "The daily meal allowance during business travel is 55 euros. Alcohol is not reimbursed.",
                "Receipts are required for every claim above 20 euros.",
            ]),
            ("Approval limits", [
                "Team leads can approve expenses up to 750 euros. Anything above 750 euros requires approval from a department head.",
                "Claims must be submitted within 45 days of the expense date.",
            ]),
        ],
    ),
    (
        "it-security-policy", "company", "pdf", "Information Security Policy", "2025-02-10", False,
        [
            ("Purpose", [
                "This policy defines how Northwind Logistics protects its information systems and data. It applies to all employees, contractors and systems.",
                "The Security Office owns this policy and reviews it every 12 months. Deviations require written approval from the Chief Information Security Officer.",
            ]),
            ("Passwords and multi-factor authentication", [
                "Passwords must be at least 14 characters long. Password reuse across company systems is not allowed.",
                "Multi-factor authentication is mandatory for email, the VPN and every administrative console. Hardware security keys are issued to all engineers with production access.",
                "Passwords are rotated only after a suspected compromise, not on a fixed schedule.",
            ]),
            ("Devices", [
                "Company laptops use full-disk encryption and are locked automatically after 5 minutes of inactivity. Personal devices may not store customer data.",
                "Lost or stolen devices must be reported to the Security Office within 2 hours of discovery so that they can be wiped remotely.",
            ]),
            ("Data classification", [
                "Data is classified as Public, Internal, Confidential or Restricted. Customer shipment records are Confidential. Employee payroll data is Restricted.",
                "Restricted data may only be shared with named recipients and must never be sent over unencrypted channels.",
            ]),
            ("Security incidents", [
                "Any suspected security incident must be reported to the Security Office through the incident hotline or the security mailbox. Employees must not attempt to investigate incidents on their own.",
                "The Security Office acknowledges reports within 30 minutes during business hours and within 2 hours outside them.",
            ]),
            ("Third-party access", [
                "Vendors receive access only to the systems named in their contract and only for the duration of the engagement. Vendor accounts expire automatically after 90 days unless renewed by the sponsoring manager.",
            ]),
        ],
    ),
    (
        "remote-work-policy", "company", "pdf", "Remote Work Policy", "2024-09-01", False,
        [
            ("Who can work remotely", [
                "Office-based employees may work from home up to 3 days per week with their manager's agreement. Depot and driving roles are not eligible because their work requires physical presence.",
            ]),
            ("Equipment and expenses", [
                "Northwind Logistics provides a laptop and a monitor for approved remote workers. The monthly home-office stipend is 35 euros.",
                "Employees are responsible for a stable internet connection and a safe workspace.",
            ]),
            ("Core hours", [
                "Remote employees must be available between 10:00 and 15:00 local time on working days. Meetings are scheduled inside these core hours where possible.",
            ]),
        ],
    ),
    (
        "travel-policy", "company", "docx", "Business Travel Policy", "2024-11-20", False,
        [
            ("Booking", [
                "All business travel must be booked through the Northwind travel portal. Flights over 6 hours may be booked in premium economy.",
                "Hotel bookings are capped at 160 euros per night in major cities and 120 euros elsewhere.",
            ]),
            ("Rental cars", [
                "Rental cars are permitted only when public transport is not practical. The car class must be compact or smaller unless three or more colleagues travel together.",
            ]),
            ("Travel insurance", [
                "All employees on business trips are covered by the corporate travel insurance policy. Claims are handled by the People Team.",
            ]),
        ],
    ),
    (
        "code-of-conduct", "company", "docx", "Code of Conduct", "2024-05-05", False,
        [
            ("Our values", [
                "We treat customers, colleagues and partners with respect. We deliver shipments safely and on time, and we tell the truth when something goes wrong.",
            ]),
            ("Gifts and hospitality", [
                "Employees may not accept gifts worth more than 50 euros from suppliers or customers. Gifts above this value must be declined or handed to the Compliance Officer.",
            ]),
            ("Conflicts of interest", [
                "Employees must declare any financial interest in a supplier or competitor to their manager in writing. Managers forward declarations to the Compliance Officer within 5 working days.",
            ]),
            ("Reporting concerns", [
                "Concerns can be raised with a manager, the People Team or the confidential ethics line. Retaliation against anyone who reports a concern in good faith is a disciplinary offence.",
            ]),
        ],
    ),
    (
        "onboarding-guide", "company", "docx", "New Joiner Onboarding Guide", "2025-01-08", False,
        [
            ("Your first week", [
                "On your first day you collect your laptop and badge from the front desk and meet your onboarding buddy. IT accounts are created before you arrive.",
                "In the first week you complete the mandatory security awareness training and the health and safety induction.",
            ]),
            ("Probation", [
                "The probation period lasts 3 months. You and your manager review progress at 6 weeks and again at 12 weeks.",
            ]),
            ("Who to ask", [
                "Questions about pay or contracts go to the People Team. Questions about equipment go to the IT service desk, which is open from 07:00 to 19:00 on weekdays.",
            ]),
        ],
    ),
    (
        "data-retention-policy", "company", "pdf", "Data Retention Policy", "2024-07-15", False,
        [
            ("Principle", [
                "Northwind Logistics keeps data only as long as it is needed for a legitimate business or legal purpose.",
            ]),
            ("Retention periods", [
                "Shipment records are kept for 7 years. Customer support tickets are kept for 3 years.",
                "Application logs containing personal data are kept for 90 days. CCTV footage from depots is kept for 30 days.",
            ]),
            ("Deletion", [
                "Expired data is deleted automatically each month. Deletion requests from customers are completed within 30 days.",
            ]),
        ],
    ),
    (
        "incident-response-sop", "company", "docx", "Incident Response Procedure", "2025-04-02", False,
        [
            ("Severity levels", [
                "Severity 1 means a complete outage of shipment tracking or a confirmed data breach. Severity 2 means a partial outage affecting one region. Severity 3 covers minor issues with a workaround.",
            ]),
            ("Response targets", [
                "For Severity 1 incidents the on-call engineer must acknowledge within 10 minutes and a status update is published every 30 minutes. For Severity 2 incidents the acknowledgement target is 30 minutes.",
            ]),
            ("After the incident", [
                "A written post-incident review is completed within 5 working days for every Severity 1 and Severity 2 incident. Reviews are blameless and focus on how to prevent recurrence.",
            ]),
        ],
    ),
    (
        "company-faq", "company", "md", "Company FAQ", "2025-02-01", False,
        [
            ("Where is the head office?", [
                "The head office is in the fictional city of Marlow Bay. It houses the executive team, finance and the People Team.",
            ]),
            ("How many depots do we operate?", [
                "Northwind Logistics operates 20 depots across the region. Each depot has its own handbook in the document library.",
            ]),
            ("How do I reset my password?", [
                "Use the self-service portal or call the IT service desk. Resets require multi-factor authentication.",
            ]),
        ],
    ),
    (
        "public-holidays-2025", "company", "html", "Public Holidays 2025", "2025-01-02", False,
        [
            ("Holiday calendar", [
                "Depots and offices are closed on 1 January, 18 April, 21 April, 1 May, 25 December and 26 December. Emergency dispatch remains staffed on all holidays.",
            ]),
            ("Holiday working", [
                "Employees who work on a public holiday receive double pay and one additional day off in lieu.",
            ]),
        ],
    ),
    (
        "whistleblower-policy", "company", "txt", "Whistleblower Policy", "2024-03-10", False,
        [
            ("Purpose", [
                "This policy protects employees who report suspected wrongdoing. Reports can be made anonymously through the ethics line.",
            ]),
            ("Protection", [
                "No employee will suffer detriment for a report made in good faith. The Compliance Officer acknowledges every report within 7 days.",
            ]),
        ],
    ),
    # ---------------- HR-only collection ----------------
    (
        "salary-bands", "hr", "pdf", "Salary Bands (Confidential)", "2025-01-15", False,
        [
            ("Engineering bands", [
                "The salary band for L3 engineers is 52,000 to 64,000 euros per year. The salary band for L4 engineers is 64,000 to 80,000 euros per year.",
                "The salary band for L5 engineers is 80,000 to 102,000 euros per year. The salary band for L6 staff engineers is 102,000 to 128,000 euros per year.",
            ]),
            ("Operations bands", [
                "The salary band for depot supervisors is 38,000 to 46,000 euros per year. The salary band for depot managers is 54,000 to 68,000 euros per year.",
            ]),
            ("Handling", [
                "Salary bands are restricted to the People Team and must not be shared with employees outside it.",
            ]),
        ],
    ),
    (
        "compensation-policy", "hr", "docx", "Compensation Policy", "2025-01-20", False,
        [
            ("Annual review", [
                "Salaries are reviewed once per year in April. Merit increases are budgeted at 3.5 percent of payroll.",
            ]),
            ("Bonus", [
                "The annual bonus target is 8 percent of base salary for individual contributors and 15 percent for managers. Bonuses are paid in the March payroll.",
            ]),
        ],
    ),
    (
        "performance-review-process", "hr", "docx", "Performance Review Process", "2024-10-01", False,
        [
            ("Cycle", [
                "Performance reviews run twice a year, in June and December. Managers submit ratings on a five-point scale.",
            ]),
            ("Calibration", [
                "Calibration sessions are held by department heads and the People Team. No more than 15 percent of a team may receive the top rating.",
            ]),
            ("Improvement plans", [
                "An employee rated in the lowest category enters a 60-day performance improvement plan agreed with the People Team.",
            ]),
        ],
    ),
    (
        "disciplinary-procedure", "hr", "pdf", "Disciplinary Procedure", "2024-06-12", False,
        [
            ("Stages", [
                "The procedure has three stages: a verbal warning, a written warning and a final written warning. Dismissal may follow if conduct does not improve.",
            ]),
            ("Investigation", [
                "An investigation must be completed within 15 working days of the complaint. The employee has the right to be accompanied by a colleague at any disciplinary meeting.",
            ]),
        ],
    ),
    (
        "recruitment-sop", "hr", "docx", "Recruitment Procedure", "2024-12-03", False,
        [
            ("Approval", [
                "Every new position requires a headcount approval from the Finance Director before it is advertised.",
            ]),
            ("Interviews", [
                "Candidates complete a screening call, a technical or practical assessment and a final panel interview. Interview notes are kept for 6 months.",
            ]),
            ("Offers", [
                "Offers are issued by the People Team within 2 working days of the final decision. Offers above the salary band require written approval from the Finance Director.",
            ]),
        ],
    ),
    (
        "benefits-guide", "hr", "docx", "Employee Benefits Guide", "2025-01-05", False,
        [
            ("Pension", [
                "Northwind Logistics contributes 6 percent of base salary to the employee pension scheme when the employee contributes at least 4 percent.",
            ]),
            ("Health cover", [
                "Private health cover is available to all permanent employees after probation. Family cover costs employees 45 euros per month.",
            ]),
            ("Learning budget", [
                "Each employee has an annual learning budget of 1,200 euros for courses and conferences, approved by their manager.",
            ]),
        ],
    ),
    (
        "termination-and-offboarding", "hr", "pdf", "Termination and Offboarding", "2024-08-19", False,
        [
            ("Notice periods", [
                "The notice period is 1 month for employees with less than 2 years of service and 3 months for employees with 2 or more years of service.",
            ]),
            ("Offboarding", [
                "On the last working day the employee returns all equipment and IT access is removed by 18:00. Final pay is processed in the next payroll run.",
            ]),
        ],
    ),
    (
        "grievance-policy", "hr", "md", "Grievance Policy", "2024-04-22", False,
        [
            ("Raising a grievance", [
                "Employees raise a grievance in writing to the People Team. The People Team acknowledges it within 3 working days.",
            ]),
            ("Outcome", [
                "A hearing is held within 10 working days and the outcome is communicated in writing within 5 working days of the hearing. Employees may appeal within 7 days.",
            ]),
        ],
    ),
    # ---------------- Engineering collection ----------------
    (
        "coding-standards", "engineering", "md", "Coding Standards", "2025-03-15", False,
        [
            ("Style", [
                "All services use the shared formatter and linter configuration. Code that fails the linter cannot be merged.",
            ]),
            ("Code review", [
                "Every pull request needs at least 2 approving reviews, one of which must come from the owning team. Pull requests should stay under 400 changed lines.",
            ]),
            ("Testing", [
                "New logic requires unit tests. The minimum line coverage for services is 80 percent.",
            ]),
        ],
    ),
    (
        "deployment-runbook", "engineering", "docx", "Deployment Runbook", "2025-02-25", False,
        [
            ("Release windows", [
                "Production deployments happen Monday to Thursday between 09:00 and 16:00. There is a deployment freeze on Fridays and during the last week of December.",
            ]),
            ("Rollout", [
                "Services roll out to 5 percent of traffic first, then 25 percent, then 100 percent, with a 15-minute soak between stages.",
            ]),
            ("Rollback", [
                "Rollback is triggered automatically if the error rate exceeds 2 percent for 5 minutes. Engineers can also roll back manually with the deploy tool.",
            ]),
        ],
    ),
    (
        "on-call-handbook", "engineering", "pdf", "On-Call Handbook", "2025-01-30", False,
        [
            ("Rotation", [
                "On-call rotations last 7 days and start on Mondays at 10:00. Each rotation has a primary and a secondary engineer.",
            ]),
            ("Compensation", [
                "Engineers receive a flat on-call allowance of 350 euros per week plus time off in lieu for any night-time call-out lasting more than 1 hour.",
            ]),
            ("Escalation", [
                "If the primary does not acknowledge a page within 10 minutes it escalates to the secondary and then to the engineering manager.",
            ]),
        ],
    ),
    (
        "architecture-overview", "engineering", "html", "Platform Architecture Overview", "2025-03-01", False,
        [
            ("Services", [
                "The tracking platform consists of an ingestion gateway, a routing service, a notification service and a reporting database.",
            ]),
            ("Data stores", [
                "Shipment events are stored in a PostgreSQL cluster with a read replica for reporting. Cached lookups use Redis with a 10-minute expiry.",
            ]),
            ("Availability targets", [
                "The tracking API targets 99.9 percent monthly availability. The routing service targets a p95 latency of 250 milliseconds.",
            ]),
        ],
    ),
    (
        "api-guidelines", "engineering", "md", "API Guidelines", "2024-12-10", False,
        [
            ("Versioning", [
                "APIs are versioned in the URL path, for example /v2/shipments. Breaking changes require a new major version and a 6-month deprecation period.",
            ]),
            ("Pagination", [
                "List endpoints use cursor pagination with a default page size of 50 and a maximum of 200.",
            ]),
            ("Errors", [
                "Errors return a JSON body with a machine-readable code and a human-readable message. Internal stack traces are never returned.",
            ]),
        ],
    ),
    (
        "postmortem-template", "engineering", "md", "Post-Incident Review Template", "2024-09-30", False,
        [
            ("Summary", [
                "Describe what happened, the customer impact and the duration in plain language.",
            ]),
            ("Timeline", [
                "List key events with timestamps in UTC, including detection, escalation, mitigation and resolution.",
            ]),
            ("Actions", [
                "Every action item has an owner and a due date. Actions are tracked until closed and reviewed in the monthly reliability meeting.",
            ]),
        ],
    ),
    (
        "secrets-management", "engineering", "docx", "Secrets Management Guide", "2025-02-14", False,
        [
            ("Storage", [
                "Secrets are stored in the central vault and never in source control or chat. Services read secrets at startup through their workload identity.",
            ]),
            ("Rotation", [
                "Database credentials rotate every 90 days. API keys for third-party services rotate every 180 days or immediately after suspected exposure.",
            ]),
        ],
    ),
    (
        "database-operations", "engineering", "pdf", "Database Operations Guide", "2025-03-20", False,
        [
            ("Backups", [
                "Databases are backed up nightly and transaction logs are archived continuously. Backups are retained for 35 days.",
            ]),
            ("Restore drills", [
                "A full restore drill is performed every quarter. The recovery time objective is 4 hours and the recovery point objective is 15 minutes.",
            ]),
            ("Maintenance", [
                "Schema migrations run through the migration tool and must be backwards compatible with the previous release.",
            ]),
        ],
    ),
]
