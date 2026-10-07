from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.models import AttendanceRecord
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.expenses.models import Expense
from apps.inventory.models import StockCategory, StockItem, StockMovement, Supplier
from apps.invoices.models import Invoice, InvoiceItem
from apps.jobs.models import Job, JobActivity
from apps.notifications.models import Notification
from apps.payments.models import Payment
from apps.roles.models import Role
from apps.vehicles.models import Vehicle


CUSTOMERS = [
    ("Rahul Nair", "9847011201", "rahul.nair@example.test", "Toyota", "Innova Crysta", "KL08BQ4581", 2022, "Diesel"),
    ("Fahad K", "9746123402", "fahad.k@example.test", "Hyundai", "Creta", "KL10AX1134", 2023, "Diesel"),
    ("Niya Motors", "9895123403", "service@niyamotors.example.test", "Maruti Suzuki", "Baleno", "KL07CS9902", 2021, "Petrol"),
    ("Arjun Menon", "9633012204", "arjun.menon@example.test", "Honda", "City", "KL45M7788", 2020, "Petrol"),
    ("Shameer Ali", "9567012205", "shameer.ali@example.test", "Kia", "Seltos", "KL55R2201", 2024, "Diesel"),
    ("Anjali Das", "9447012206", "anjali.das@example.test", "Tata", "Nexon", "KL64P4321", 2022, "Petrol"),
    ("Riyas P", "9074012207", "riyas.p@example.test", "Mahindra", "XUV700", "KL53W8080", 2023, "Diesel"),
    ("Meera Joseph", "8921012208", "meera.joseph@example.test", "Renault", "Kiger", "KL41T6116", 2021, "Petrol"),
]

STOCK = [
    ("ENG-OIL-5W30", "5W-30 Fully Synthetic Engine Oil 4L", "Lubricants", "Shell", Decimal("1850"), Decimal("2450"), Decimal("18"), Decimal("3"), Decimal("6"), "A-01"),
    ("OIL-FLT-001", "Premium Oil Filter", "Filters", "Bosch", Decimal("310"), Decimal("520"), Decimal("4"), Decimal("1"), Decimal("8"), "B-02"),
    ("AIR-FLT-001", "Engine Air Filter", "Filters", "MANN", Decimal("480"), Decimal("790"), Decimal("12"), Decimal("2"), Decimal("5"), "B-04"),
    ("BRK-PAD-FR", "Front Brake Pad Set", "Brakes", "Brembo", Decimal("1650"), Decimal("2450"), Decimal("5"), Decimal("1"), Decimal("6"), "C-01"),
    ("CLN-THR-01", "Throttle Body Cleaner", "Consumables", "Wurth", Decimal("340"), Decimal("550"), Decimal("16"), Decimal("0"), Decimal("5"), "D-03"),
    ("AC-FLT-001", "Cabin AC Filter", "Filters", "Bosch", Decimal("390"), Decimal("690"), Decimal("3"), Decimal("0"), Decimal("6"), "B-05"),
]

STAFF = [
    ("CG-DEMO-001", "Akhil Raj", "Mechanic", "mechanic", "demo.mechanic@example.test", "9000011101", Decimal("26000")),
    ("CG-DEMO-002", "Nabeel P", "Senior Mechanic", "mechanic", "demo.senior@example.test", "9000011102", Decimal("32000")),
    ("CG-DEMO-003", "Diya Thomas", "Service Advisor", "advisor", "demo.advisor@example.test", "9000011103", Decimal("28000")),
]


def aware_today_at(hour, minute=0):
    today = timezone.localdate()
    return timezone.make_aware(datetime.combine(today, time(hour, minute)))


class Command(BaseCommand):
    help = "Seed realistic, non-destructive demo data into the company owned by a target user email."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="micew@mailinator.com",
            help="Existing CubixGear account email whose company should receive demo data.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        owner = (
            User.objects.select_related("company", "branch", "role")
            .filter(email__iexact=email)
            .first()
        )

        if not owner:
            raise CommandError(f"No CubixGear user found for {email}. Sign up first.")

        if not owner.company:
            raise CommandError(f"{email} is not linked to a company.")

        company = owner.company
        branch = owner.branch or company.branches.filter(is_head_office=True).first() or company.branches.first()
        if not branch:
            raise CommandError("The company does not have a branch.")

        # First public-signup owner always stays tenant super admin.
        if owner.role:
            owner.role.name = "Super Admin"
            owner.role.code = "SUPER_ADMIN"
            owner.role.permissions = ["*"]
            owner.role.is_system = True
            owner.role.is_active = True
            owner.role.save(update_fields=["name", "code", "permissions", "is_system", "is_active"])

        owner.is_staff = False
        owner.is_superuser = False
        owner.save(update_fields=["is_staff", "is_superuser"])

        mechanic_role, _ = Role.objects.get_or_create(
            company=company,
            code="MECHANIC",
            defaults={
                "name": "Mechanic",
                "permissions": [
                    "dashboard.view",
                    "attendance.self",
                    "customers.view",
                    "vehicles.view",
                    "jobs.view",
                    "jobs.edit",
                    "stock.view",
                    "services.view",
                    "notifications.view",
                ],
                "is_system": False,
                "is_active": True,
            },
        )
        advisor_role, _ = Role.objects.get_or_create(
            company=company,
            code="SERVICE_ADVISOR",
            defaults={
                "name": "Service Advisor",
                "permissions": [
                    "dashboard.view",
                    "attendance.self",
                    "customers.view",
                    "customers.create",
                    "customers.edit",
                    "vehicles.view",
                    "vehicles.create",
                    "vehicles.edit",
                    "jobs.view",
                    "jobs.create",
                    "jobs.edit",
                    "invoices.view",
                    "payments.view",
                    "services.view",
                    "notifications.view",
                ],
                "is_system": False,
                "is_active": True,
            },
        )

        owner_employee, _ = Employee.objects.get_or_create(
            company=company,
            employee_code="CG-OWNER-001",
            defaults={
                "branch": branch,
                "user": owner,
                "name": owner.name or "Workshop Owner",
                "phone": owner.phone,
                "email": owner.email,
                "designation": "Owner / Super Admin",
                "role_name": "Super Admin",
                "department_name": "Management",
                "shift_label": "09:00 AM - 06:00 PM",
                "payment_type": "Monthly",
                "joining_date": timezone.localdate() - timedelta(days=365),
                "employment_type": "Full Time",
                "status": "Active",
            },
        )
        if owner_employee.user_id != owner.id:
            owner_employee.user = owner
            owner_employee.save(update_fields=["user"])

        staff_users = []
        for code, name, designation, kind, staff_email, phone, salary in STAFF:
            role = mechanic_role if kind == "mechanic" else advisor_role
            user = User.objects.filter(email__iexact=staff_email).first()
            if not user:
                user = User.objects.create_user(
                    email=staff_email,
                    password=None,
                    name=name,
                    phone=phone,
                    company=company,
                    branch=branch,
                    role=role,
                    is_active=True,
                    is_staff=False,
                    email_verified=True,
                )
                user.set_unusable_password()
                user.save(update_fields=["password"])
            elif user.company_id == company.id:
                changed = False
                for field, value in [("branch", branch), ("role", role), ("name", name)]:
                    if getattr(user, field) != value:
                        setattr(user, field, value)
                        changed = True
                if changed:
                    user.save()

            employee, _ = Employee.objects.get_or_create(
                company=company,
                employee_code=code,
                defaults={
                    "branch": branch,
                    "user": user,
                    "name": name,
                    "phone": phone,
                    "email": staff_email,
                    "designation": designation,
                    "role_name": designation,
                    "department_name": "Workshop Operations",
                    "shift_label": "09:00 AM - 06:00 PM",
                    "payment_type": "Monthly",
                    "joining_date": timezone.localdate() - timedelta(days=220),
                    "employment_type": "Full Time",
                    "base_salary": salary,
                    "status": "Active",
                },
            )
            if employee.user_id != user.id:
                employee.user = user
                employee.save(update_fields=["user"])
            staff_users.append((user, employee))

        supplier, _ = Supplier.objects.get_or_create(
            company=company,
            name="Kerala Auto Parts",
            defaults={
                "branch": branch,
                "contact_person": "Sales Desk",
                "phone": "04872345678",
                "email": "orders@keralaautoparts.example.test",
                "address": "Thrissur, Kerala",
                "is_active": True,
            },
        )

        categories = {}
        for category_name in {"Lubricants", "Filters", "Brakes", "Consumables"}:
            categories[category_name], _ = StockCategory.objects.get_or_create(
                company=company,
                name=category_name,
                defaults={
                    "branch": branch,
                    "description": f"{category_name} workshop inventory",
                    "is_active": True,
                },
            )

        stock_items = {}
        for sku, name, category, brand, cost, price, on_hand, reserved, minimum, rack in STOCK:
            item, _ = StockItem.objects.update_or_create(
                company=company,
                sku=sku,
                defaults={
                    "branch": branch,
                    "name": name,
                    "category": categories[category],
                    "brand": brand,
                    "compatible_vehicle": "Universal",
                    "unit": "Piece",
                    "cost_price": cost,
                    "selling_price": price,
                    "on_hand": on_hand,
                    "reserved": reserved,
                    "minimum_stock": minimum,
                    "reorder_level": minimum + Decimal("3"),
                    "rack": rack,
                    "supplier": supplier,
                    "tax": Decimal("18"),
                    "status": "Active",
                },
            )
            stock_items[sku] = item

        customers = []
        vehicles = []
        today = timezone.localdate()

        for index, (name, phone, customer_email, make, model, registration, year, fuel) in enumerate(CUSTOMERS, start=1):
            customer, _ = Customer.objects.update_or_create(
                company=company,
                phone=phone,
                defaults={
                    "branch": branch,
                    "name": name,
                    "whatsapp": phone,
                    "email": customer_email,
                    "customer_type": "business" if name == "Niya Motors" else "individual",
                    "company_name": name if name == "Niya Motors" else "",
                    "address": "Kerala",
                    "city": company.city or "Thrissur",
                    "state": company.state or "Kerala",
                    "pincode": company.pincode or "680001",
                    "notes": "Realistic demo customer",
                    "status": "active",
                    "last_service_date": today - timedelta(days=45 + index * 4),
                },
            )
            vehicle, _ = Vehicle.objects.update_or_create(
                company=company,
                registration=registration,
                defaults={
                    "branch": branch,
                    "customer": customer,
                    "make": make,
                    "model": model,
                    "year": year,
                    "fuel_type": fuel,
                    "transmission": "Automatic" if index % 2 == 0 else "Manual",
                    "color": ["White", "Black", "Silver", "Blue"][index % 4],
                    "odometer": 18000 + index * 7350,
                    "last_service_date": today - timedelta(days=45 + index * 4),
                    "next_service_due": today + timedelta(days=30 + index * 6),
                    "insurance_expiry": today + timedelta(days=90 + index * 20),
                    "status": "Active",
                },
            )
            customers.append(customer)
            vehicles.append(vehicle)

        statuses = [
            Job.STATUS_IN_PROGRESS,
            Job.STATUS_INSPECTION,
            Job.STATUS_READY,
            Job.STATUS_WAITING_PARTS,
            Job.STATUS_QC,
            Job.STATUS_APPROVED,
            Job.STATUS_DELIVERED,
            Job.STATUS_ESTIMATE_PENDING,
        ]
        service_names = [
            "Periodic Service & Oil Change",
            "Brake Inspection & Pad Replacement",
            "AC Cooling Check",
            "Suspension Noise Diagnosis",
            "Engine Warning Light Diagnosis",
            "Battery & Charging System Check",
            "Full Service",
            "Clutch Inspection",
        ]

        jobs = []
        for index, (customer, vehicle) in enumerate(zip(customers, vehicles), start=1):
            technician = staff_users[(index - 1) % 2][0]
            status_value = statuses[index - 1]
            total = Decimal("2800") + Decimal(index * 1450)
            job, _ = Job.objects.update_or_create(
                company=company,
                job_number=f"DEMO-JOB-{index:04d}",
                defaults={
                    "branch": branch,
                    "customer": customer,
                    "vehicle": vehicle,
                    "advisor": staff_users[2][0],
                    "technician": technician,
                    "status": status_value,
                    "priority": "High" if index in {4, 5} else "Normal",
                    "odometer": vehicle.odometer,
                    "fuel_level": "Half",
                    "promised_at": timezone.now() + timedelta(hours=index + 2),
                    "delivered_at": timezone.now() - timedelta(days=1) if status_value == Job.STATUS_DELIVERED else None,
                    "complaints": [service_names[index - 1]],
                    "inspection": {"status": "Completed" if index > 1 else "In Progress"},
                    "work": [{"description": service_names[index - 1], "status": status_value}],
                    "notes": "Realistic demo job for UI testing",
                    "estimate_total": total,
                    "labour_total": (total * Decimal("0.35")).quantize(Decimal("0.01")),
                    "parts_total": (total * Decimal("0.65")).quantize(Decimal("0.01")),
                },
            )
            jobs.append(job)

            JobActivity.objects.get_or_create(
                company=company,
                job=job,
                event="Demo Job Updated",
                defaults={
                    "branch": branch,
                    "description": f"{job.job_number} moved to {job.status}",
                    "actor": owner,
                    "to_status": job.status,
                    "metadata": {"demo": True},
                },
            )

        invoice_totals = [
            Decimal("5900"), Decimal("8250"), Decimal("4200"), Decimal("12600"),
            Decimal("7350"), Decimal("4800"),
        ]
        invoices = []
        for index, total in enumerate(invoice_totals, start=1):
            customer = customers[index - 1]
            vehicle = vehicles[index - 1]
            job = jobs[index - 1]
            paid = total if index in {1, 3} else (total * Decimal("0.45") if index in {2, 5} else Decimal("0"))
            balance = total - paid

            invoice, _ = Invoice.objects.update_or_create(
                company=company,
                number=f"DEMO-INV-{index:04d}",
                defaults={
                    "branch": branch,
                    "kind": "invoice",
                    "invoice_type": "gst",
                    "status": "Paid" if balance <= 0 else ("Partially Paid" if paid > 0 else "Finalized"),
                    "date": today - timedelta(days=index - 1),
                    "customer": customer,
                    "vehicle": vehicle,
                    "job": job,
                    "tax_mode": "gst",
                    "cgst_rate": Decimal("9"),
                    "sgst_rate": Decimal("9"),
                    "taxable": (total / Decimal("1.18")).quantize(Decimal("0.01")),
                    "cgst": (total - (total / Decimal("1.18"))) / Decimal("2"),
                    "sgst": (total - (total / Decimal("1.18"))) / Decimal("2"),
                    "total": total,
                    "paid": paid,
                    "balance": balance,
                    "payment_mode": "UPI" if paid > 0 else "",
                    "staff_name": staff_users[2][0].name,
                    "notes": "Demo invoice",
                },
            )
            invoices.append(invoice)

            if not invoice.items.exists():
                InvoiceItem.objects.create(
                    invoice=invoice,
                    item_type="Service",
                    description=service_names[index - 1],
                    code=f"SRV-{index:03d}",
                    hsn_sac="998729",
                    quantity=Decimal("1"),
                    unit="JOB",
                    rate=invoice.taxable,
                    tax_rate=Decimal("18"),
                )

            if paid > 0:
                Payment.objects.update_or_create(
                    company=company,
                    reference=f"DEMO-UPI-{index:04d}",
                    defaults={
                        "branch": branch,
                        "customer": customer,
                        "invoice": invoice,
                        "date": invoice.date,
                        "amount": paid,
                        "method": "UPI",
                        "status": "Completed",
                        "notes": "Demo customer payment",
                        "recorded_by": owner,
                    },
                )

        for index, (category, vendor, description, amount) in enumerate([
            ("Utilities", "KSEB", "Workshop electricity bill", Decimal("6850")),
            ("Consumables", "Local Supplies", "Cleaning and workshop consumables", Decimal("2450")),
            ("Rent", "Property Owner", "Monthly workshop rent", Decimal("28000")),
            ("Transport", "Fuel Station", "Pickup and delivery fuel", Decimal("3200")),
        ], start=1):
            Expense.objects.update_or_create(
                company=company,
                reference=f"DEMO-EXP-{index:03d}",
                defaults={
                    "branch": branch,
                    "date": today - timedelta(days=index * 2),
                    "category": category,
                    "vendor": vendor,
                    "description": description,
                    "amount": amount,
                    "tax": Decimal("0"),
                    "payment_method": "UPI",
                    "status": "Paid",
                    "created_by": owner,
                },
            )

        # Owner attendance enables the dashboard clock-in/out and My Attendance pages.
        for days_ago in range(0, 12):
            day = today - timedelta(days=days_ago)
            if day.weekday() == 6:
                continue
            clock_in = timezone.make_aware(datetime.combine(day, time(9, 5 + (days_ago % 4) * 3)))
            clock_out = None if days_ago == 0 else timezone.make_aware(datetime.combine(day, time(18, 5)))
            worked = 0 if not clock_out else int((clock_out - clock_in).total_seconds() // 60)
            AttendanceRecord.objects.update_or_create(
                employee=owner_employee,
                date=day,
                defaults={
                    "company": company,
                    "branch": branch,
                    "clock_in": clock_in,
                    "clock_out": clock_out,
                    "worked_minutes": worked,
                    "late_minutes": max(0, (clock_in.hour * 60 + clock_in.minute) - 9 * 60 - 15),
                    "overtime_minutes": max(0, worked - 540),
                    "status": "Present",
                    "location": {"label": "Main Workshop"},
                    "notes": "Demo attendance",
                },
            )

        for index, (user, employee) in enumerate(staff_users):
            AttendanceRecord.objects.update_or_create(
                employee=employee,
                date=today,
                defaults={
                    "company": company,
                    "branch": branch,
                    "clock_in": aware_today_at(8, 55 + index * 5),
                    "worked_minutes": 0,
                    "status": "Present",
                    "location": {"label": "Main Workshop"},
                },
            )

        # Stock movements make recent workshop activity look realistic.
        movement_specs = [
            ("ENG-OIL-5W30", jobs[0], Decimal("-1"), "job_issue"),
            ("OIL-FLT-001", jobs[0], Decimal("-1"), "job_issue"),
            ("BRK-PAD-FR", jobs[1], Decimal("-1"), "job_issue"),
        ]
        for sku, job, quantity, movement_type in movement_specs:
            item = stock_items[sku]
            StockMovement.objects.get_or_create(
                company=company,
                reference=f"DEMO-{job.job_number}-{sku}",
                defaults={
                    "branch": branch,
                    "item": item,
                    "movement_type": movement_type,
                    "quantity": quantity,
                    "unit_cost": item.cost_price,
                    "job": job,
                    "note": "Demo workshop issue",
                    "created_by": owner,
                },
            )

        notifications = [
            ("Low stock: Premium Oil Filter", "Only 3 available after reservations.", "warning", {"path": "/stock"}),
            ("Vehicle ready for delivery", f"{vehicles[2].registration} is ready for customer pickup.", "success", {"path": f"/jobs/{jobs[2].id}"}),
            ("Outstanding invoice follow-up", "Two demo invoices are partially paid and need follow-up.", "info", {"path": "/invoices"}),
        ]
        for title, message, notification_type, data in notifications:
            Notification.objects.get_or_create(
                company=company,
                user=owner,
                title=title,
                defaults={
                    "branch": branch,
                    "message": message,
                    "notification_type": notification_type,
                    "data": {**data, "demo": True},
                    "is_read": False,
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f"Realistic demo data ready for {email} / {company.name}: "
            f"{len(customers)} customers, {len(vehicles)} vehicles, {len(jobs)} jobs, "
            f"{len(invoices)} invoices, {len(stock_items)} stock items and {len(staff_users)} staff."
        ))
