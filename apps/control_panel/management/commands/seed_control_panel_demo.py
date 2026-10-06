from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.companies.models import Company
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.inventory.models import StockCategory, StockItem
from apps.invoices.models import Invoice
from apps.jobs.models import Job
from apps.roles.models import Role
from apps.saas.models import SecurityEvent, StorageUsage, Subscription
from apps.vehicles.models import Vehicle


DEMO_COMPANIES = [
    {
        "name": "AutoNest Motors",
        "slug": "autonest-motors",
        "email": "hello@autonest.demo",
        "phone": "9876501001",
        "city": "Kochi",
        "state": "Kerala",
        "gstin": "32AAAAA1111A1Z1",
        "plan": "growth",
        "amount": Decimal("2499"),
    },
    {
        "name": "Malabar AutoCare",
        "slug": "malabar-autocare",
        "email": "hello@malabar.demo",
        "phone": "9876501002",
        "city": "Kozhikode",
        "state": "Kerala",
        "gstin": "32BBBBB2222B1Z2",
        "plan": "pro",
        "amount": Decimal("3999"),
    },
    {
        "name": "Metro Garage",
        "slug": "metro-garage",
        "email": "hello@metro.demo",
        "phone": "9876501003",
        "city": "Thrissur",
        "state": "Kerala",
        "gstin": "32CCCCC3333C1Z3",
        "plan": "starter",
        "amount": Decimal("1499"),
    },
    {
        "name": "DrivePoint Workshop",
        "slug": "drivepoint-workshop",
        "email": "hello@drivepoint.demo",
        "phone": "9876501004",
        "city": "Malappuram",
        "state": "Kerala",
        "gstin": "32DDDDD4444D1Z4",
        "plan": "growth",
        "amount": Decimal("2499"),
    },
]


class Command(BaseCommand):
    help = "Create safe, idempotent demo data for the CubixGear SaaS Control Panel."

    def handle(self, *args, **options):
        today = timezone.localdate()
        now = timezone.now()

        for index, info in enumerate(DEMO_COMPANIES, start=1):
            company, _ = Company.objects.get_or_create(
                slug=info["slug"],
                defaults={
                    "name": info["name"],
                    "legal_name": info["name"],
                    "email": info["email"],
                    "phone": info["phone"],
                    "gstin": info["gstin"],
                    "address": f"{index * 10} Demo Road",
                    "city": info["city"],
                    "state": info["state"],
                    "country": "India",
                    "pincode": f"67{index}001",
                    "currency": "INR",
                    "plan": info["plan"],
                    "is_active": True,
                },
            )

            branch, _ = Branch.objects.get_or_create(
                company=company,
                code="HO",
                defaults={
                    "name": "Head Office",
                    "phone": info["phone"],
                    "email": info["email"],
                    "city": info["city"],
                    "state": info["state"],
                    "is_head_office": True,
                    "is_active": True,
                },
            )

            role, _ = Role.objects.get_or_create(
                company=company,
                code="ADMIN",
                defaults={
                    "name": "Workshop Admin",
                    "permissions": ["*"],
                    "is_system": False,
                    "is_active": True,
                },
            )

            email = f"owner{index}@demo.cubixgear.local"
            owner = User.objects.filter(email=email).first()
            if not owner:
                owner = User.objects.create_user(
                    email=email,
                    password=None,
                    name=f"{info['name']} Owner",
                    phone=f"90000010{index}",
                    company=company,
                    branch=branch,
                    role=role,
                    is_active=True,
                    is_staff=False,
                    email_verified=True,
                )

            Subscription.objects.get_or_create(
                company=company,
                branch=branch,
                plan=info["plan"].title(),
                defaults={
                    "status": "Active",
                    "billing_cycle": "monthly",
                    "amount": info["amount"],
                    "currency": "INR",
                    "starts_at": now - timedelta(days=90 + index * 5),
                    "renews_at": now + timedelta(days=20 + index),
                    "seats": 5 + index * 2,
                },
            )

            StorageUsage.objects.get_or_create(
                company=company,
                branch=branch,
                date=today,
                category="media",
                defaults={
                    "bytes_used": (index * 2_500_000_000),
                    "file_count": 120 * index,
                    "metadata": {"source": "demo"},
                },
            )

            category, _ = StockCategory.objects.get_or_create(
                company=company,
                branch=branch,
                name="Service Parts",
                defaults={"description": "Demo stock category", "is_active": True},
            )

            for item_index, item_name in enumerate(["Engine Oil 5W30", "Oil Filter", "Brake Pad Set"], start=1):
                StockItem.objects.get_or_create(
                    company=company,
                    branch=branch,
                    sku=f"{index}-DEMO-{item_index:03d}",
                    defaults={
                        "name": item_name,
                        "category": category,
                        "brand": "DemoBrand",
                        "unit": "Piece",
                        "cost_price": Decimal("450") * item_index,
                        "selling_price": Decimal("650") * item_index,
                        "on_hand": Decimal(str(item_index + index)),
                        "reserved": Decimal("1"),
                        "minimum_stock": Decimal("3"),
                        "reorder_level": Decimal("5"),
                        "rack": f"A-{item_index}",
                        "status": "Active",
                    },
                )

            for customer_index in range(1, 4):
                customer, _ = Customer.objects.get_or_create(
                    company=company,
                    phone=f"910{index}0000{customer_index}",
                    defaults={
                        "branch": branch,
                        "name": f"Demo Customer {index}-{customer_index}",
                        "email": f"customer{index}{customer_index}@demo.local",
                        "city": info["city"],
                        "state": info["state"],
                        "status": "active",
                    },
                )

                vehicle, _ = Vehicle.objects.get_or_create(
                    company=company,
                    registration=f"KL{index:02d}DM{customer_index:04d}",
                    defaults={
                        "branch": branch,
                        "customer": customer,
                        "make": ["Maruti", "Hyundai", "Toyota"][customer_index - 1],
                        "model": ["Swift", "i20", "Glanza"][customer_index - 1],
                        "year": 2022 + (customer_index % 2),
                        "fuel_type": "Petrol",
                        "odometer": 18000 * customer_index,
                        "status": "Active",
                    },
                )

                status_values = [
                    Job.STATUS_INSPECTION,
                    Job.STATUS_IN_PROGRESS,
                    Job.STATUS_READY,
                ]
                job, _ = Job.objects.get_or_create(
                    company=company,
                    job_number=f"DEMO-{index}-{customer_index:03d}",
                    defaults={
                        "branch": branch,
                        "customer": customer,
                        "vehicle": vehicle,
                        "advisor": owner,
                        "status": status_values[customer_index - 1],
                        "priority": "Normal" if customer_index < 3 else "High",
                        "odometer": vehicle.odometer,
                        "complaints": ["Demo service complaint"],
                        "notes": "Generated by seed_control_panel_demo",
                        "estimate_total": Decimal("4500") * customer_index,
                        "labour_total": Decimal("1200") * customer_index,
                        "parts_total": Decimal("2500") * customer_index,
                    },
                )

                Invoice.objects.get_or_create(
                    company=company,
                    number=f"DEMO-INV-{index}-{customer_index:03d}",
                    defaults={
                        "branch": branch,
                        "kind": "invoice",
                        "invoice_type": "gst",
                        "status": "Finalized" if customer_index < 3 else "Draft",
                        "date": today - timedelta(days=customer_index * 2),
                        "customer": customer,
                        "vehicle": vehicle,
                        "job": job,
                        "tax_mode": "gst",
                        "taxable": Decimal("5000") * customer_index,
                        "igst": Decimal("900") * customer_index,
                        "total": Decimal("5900") * customer_index,
                        "paid": Decimal("3000") * customer_index,
                        "balance": Decimal("2900") * customer_index,
                        "payment_mode": "UPI",
                    },
                )

            for emp_index in range(1, 3):
                Employee.objects.get_or_create(
                    company=company,
                    employee_code=f"DEMO-{index}-EMP-{emp_index:02d}",
                    defaults={
                        "branch": branch,
                        "name": f"Demo Staff {index}-{emp_index}",
                        "phone": f"920{index}0000{emp_index}",
                        "email": f"staff{index}{emp_index}@demo.local",
                        "designation": "Mechanic" if emp_index == 1 else "Service Advisor",
                        "role_name": "Mechanic" if emp_index == 1 else "Service Advisor",
                        "joining_date": today - timedelta(days=300),
                        "employment_type": "Full Time",
                        "base_salary": Decimal("22000") + Decimal(emp_index * 3000),
                        "status": "Active",
                    },
                )

            SecurityEvent.objects.get_or_create(
                company=company,
                branch=branch,
                user=owner,
                event="Demo admin login",
                defaults={
                    "ip_address": f"10.0.{index}.10",
                    "user_agent": "CubixGear Demo Browser",
                    "metadata": {"source": "seed_control_panel_demo"},
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f"Demo Control Panel data ready: {len(DEMO_COMPANIES)} companies with users, jobs, invoices, stock, staff and subscriptions."
        ))
