"""Test data for Stripe webhook events."""

from stripe import util

# Raw invoice event data
_INVOICE_DATA = {
    "id": "in_1R3fyzHxQGFC458o1NqvJRXC",
    "object": "invoice",
    "account_country": "US",
    "account_name": "gvtech",
    "account_tax_ids": None,
    "amount_due": 2000,
    "amount_paid": 2000,
    "amount_remaining": 0,
    "amount_shipping": 0,
    "application": None,
    "application_fee_amount": None,
    "attempt_count": 1,
    "attempted": True,
    "auto_advance": False,
    "automatic_tax": {
        "disabled_reason": None,
        "enabled": False,
        "liability": None,
        "status": None
    },
    "automatically_finalizes_at": None,
    "billing_reason": "subscription_create",
    "charge": "ch_3R3fyzHxQGFC458o00dK0G1Y",
    "collection_method": "charge_automatically",
    "created": 1742226821,
    "currency": "usd",
    "custom_fields": None,
    "customer": "cus_RxZu2pwt6VkEhC",
    "customer_address": None,
    "customer_email": "ganeshvarmap25@gmail.com",
    "customer_name": None,
    "customer_phone": None,
    "customer_shipping": None,
    "customer_tax_exempt": "none",
    "customer_tax_ids": [],
    "default_payment_method": None,
    "default_source": None,
    "default_tax_rates": [],
    "description": None,
    "discount": None,
    "discounts": [],
    "due_date": None,
    "effective_at": 1742226821,
    "ending_balance": 0,
    "footer": None,
    "from_invoice": None,
    "hosted_invoice_url": "https://invoice.stripe.com/i/acct_1R0zFMHxQGFC458o/test_YWNjdF8xUjB6Rk1IeFFHRkM0NThvLF9SeGJCSXR1M0JmdkozTWxidkxvTndOZFQ3cmE0Uk52LDEzMjc2NzYyNA02007yWU1zaG?s=ap",
    "invoice_pdf": "https://pay.stripe.com/invoice/acct_1R0zFMHxQGFC458o/test_YWNjdF8xUjB6Rk1IeFFHRkM0NThvLF9SeGJCSXR1M0JmdkozTWxidkxvTndOZFQ3cmE0Uk52LDEzMjc2NzYyNA02007yWU1zaG/pdf?s=ap",
    "issuer": {
        "type": "self"
    },
    "last_finalization_error": None,
    "latest_revision": None,
    "lines": {
        "object": "list",
        "data": [
            {
                "id": "il_1R3fyzHxQGFC458o6Z4pE3dL",
                "object": "line_item",
                "amount": 2000,
                "amount_excluding_tax": 2000,
                "currency": "usd",
                "description": "1 × Professional Plan (at $20.00 / month)",
                "discount_amounts": [],
                "discountable": True,
                "discounts": [],
                "invoice": "in_1R3fyzHxQGFC458o1NqvJRXC",
                "livemode": False,
                "metadata": {},
                "period": {
                    "end": 1744905220,
                    "start": 1742226820
                },
                "plan": {
                    "id": "price_1R3fDhHxQGFC458ontcz0UHz",
                    "object": "plan",
                    "active": True,
                    "aggregate_usage": None,
                    "amount": 2000,
                    "amount_decimal": "2000",
                    "billing_scheme": "per_unit",
                    "created": 1742223889,
                    "currency": "usd",
                    "interval": "month",
                    "interval_count": 1,
                    "livemode": False,
                    "metadata": {},
                    "meter": None,
                    "nickname": None,
                    "product": "prod_Rxa3zD5vPl079h",
                    "tiers_mode": None,
                    "transform_usage": None,
                    "trial_period_days": None,
                    "usage_type": "licensed"
                },
                "pretax_credit_amounts": [],
                "price": {
                    "id": "price_1R3fDhHxQGFC458ontcz0UHz",
                    "object": "price",
                    "active": True,
                    "billing_scheme": "per_unit",
                    "created": 1742223889,
                    "currency": "usd",
                    "custom_unit_amount": None,
                    "livemode": False,
                    "lookup_key": None,
                    "metadata": {},
                    "nickname": None,
                    "product": "prod_Rxa3zD5vPl079h",
                    "recurring": {
                        "aggregate_usage": None,
                        "interval": "month",
                        "interval_count": 1,
                        "meter": None,
                        "trial_period_days": None,
                        "usage_type": "licensed"
                    },
                    "tax_behavior": "unspecified",
                    "tiers_mode": None,
                    "transform_quantity": None,
                    "type": "recurring",
                    "unit_amount": 2000,
                    "unit_amount_decimal": "2000"
                },
                "proration": False,
                "proration_details": {
                    "credited_items": None
                },
                "quantity": 1,
                "subscription": "sub_1R3fyyHxQGFC458oeUeioeEu",
                "subscription_item": "si_RxbBqPhntqrr4E",
                "tax_amounts": [],
                "tax_rates": [],
                "type": "subscription",
                "unit_amount_excluding_tax": "2000"
            }
        ],
        "has_more": False,
        "total_count": 1,
        "url": "/v1/invoices/in_1R3fyzHxQGFC458o1NqvJRXC/lines"
    },
    "livemode": False,
    "metadata": {},
    "next_payment_attempt": None,
    "number": "D2B1718D-0002",
    "on_behalf_of": None,
    "paid": True,
    "paid_out_of_band": False,
    "payment_intent": "pi_3R3fyzHxQGFC458o0HMydp1x",
    "payment_settings": {
        "default_mandate": None,
        "payment_method_options": None,
        "payment_method_types": None
    },
    "period_end": 1742226820,
    "period_start": 1742226820,
    "post_payment_credit_notes_amount": 0,
    "pre_payment_credit_notes_amount": 0,
    "quote": None,
    "receipt_number": None,
    "rendering": None,
    "shipping_cost": None,
    "shipping_details": None,
    "starting_balance": 0,
    "statement_descriptor": None,
    "status": "paid",
    "status_transitions": {
        "finalized_at": 1742226821,
        "marked_uncollectible_at": None,
        "paid_at": 1742226820,
        "voided_at": None
    },
    "subscription": "sub_1R3fyyHxQGFC458oeUeioeEu",
    "subscription_details": {
        "metadata": {}
    },
    "subtotal": 2000,
    "subtotal_excluding_tax": 2000,
    "tax": None,
    "test_clock": None,
    "total": 2000,
    "total_discount_amounts": [],
    "total_excluding_tax": 2000,
    "total_pretax_credit_amounts": [],
    "total_tax_amounts": [],
    "transfer_data": None,
    "webhooks_delivered_at": None
}

# Raw invoice payment failed event data
_INVOICE_PAYMENT_FAILED_DATA = {
    "account_country": "US",
    "account_name": "gvtech",
    "account_tax_ids": None,
    "amount_due": 2000,
    "amount_paid": 0,
    "amount_remaining": 2000,
    "amount_shipping": 0,
    "application": None,
    "application_fee_amount": None,
    "attempt_count": 1,
    "attempted": True,
    "auto_advance": False,
    "automatic_tax": {
        "disabled_reason": None,
        "enabled": False,
        "liability": None,
        "status": None
    },
    "automatically_finalizes_at": None,
    "billing_reason": "manual",
    "charge": "ch_3R3rNuHxQGFC458o1PgoPKIp",
    "collection_method": "charge_automatically",
    "created": 1742270649,
    "currency": "usd",
    "custom_fields": None,
    "customer": "cus_RxZu2pwt6VkEhC",  # Using same customer as success case
    "customer_address": None,
    "customer_email": None,
    "customer_name": None,
    "customer_phone": None,
    "customer_shipping": None,
    "customer_tax_exempt": "none",
    "customer_tax_ids": [],
    "default_payment_method": None,
    "default_source": None,
    "default_tax_rates": [],
    "description": "(created by Stripe CLI)",
    "discount": None,
    "discounts": [],
    "due_date": None,
    "effective_at": 1742270650,
    "ending_balance": 0,
    "footer": None,
    "from_invoice": None,
    "hosted_invoice_url": "https://invoice.stripe.com/i/acct_1R0zFMHxQGFC458o/test_YWNjdF8xUjB6Rk1IeFFHRkM0NThvLF9SeG14VW12MGY0RHB1Y24zaUtQaTN3U3V4ZGFPUXNWLDEzMjgxMTQ1Mg0200JZzCyQnx?s=ap",
    "id": "in_1R3rNtHxQGFC458ofR7bp47B",
    "invoice_pdf": "https://pay.stripe.com/invoice/acct_1R0zFMHxQGFC458o/test_YWNjdF8xUjB6Rk1IeFFHRkM0NThvLF9SeG14VW12MGY0RHB1Y24zaUtQaTN3U3V4ZGFPUXNWLDEzMjgxMTQ1Mg0200JZzCyQnx/pdf?s=ap",
    "issuer": {
        "type": "self"
    },
    "last_finalization_error": None,
    "latest_revision": None,
    "lines": {
        "data": [
            {
                "amount": 2000,
                "amount_excluding_tax": 2000,
                "currency": "usd",
                "description": "(created by Stripe CLI)",
                "discount_amounts": [],
                "discountable": True,
                "discounts": [],
                "id": "il_1R3rNtHxQGFC458owXIxRtbk",
                "invoice": "in_1R3rNtHxQGFC458ofR7bp47B",
                "invoice_item": "ii_1R3rNtHxQGFC458oL8uJ5NYG",
                "livemode": False,
                "metadata": {},
                "object": "line_item",
                "period": {
                    "end": 1742270649,
                    "start": 1742270649
                },
                "plan": None,
                "pretax_credit_amounts": [],
                "price": {
                    "active": False,
                    "billing_scheme": "per_unit",
                    "created": 1741876258,
                    "currency": "usd",
                    "custom_unit_amount": None,
                    "id": "price_1R2CmkHxQGFC458oL1QGdHwr",
                    "livemode": False,
                    "lookup_key": None,
                    "metadata": {},
                    "nickname": None,
                    "object": "price",
                    "product": "prod_Rw4wZTmr8C0I4J",
                    "recurring": None,
                    "tax_behavior": "unspecified",
                    "tiers_mode": None,
                    "transform_quantity": None,
                    "type": "one_time",
                    "unit_amount": 2000,
                    "unit_amount_decimal": "2000"
                },
                "proration": False,
                "proration_details": {
                    "credited_items": None
                },
                "quantity": 1,
                "subscription": "sub_1R3fyyHxQGFC458oeUeioeEu",  # Using same subscription as success case
                "tax_amounts": [],
                "tax_rates": [],
                "type": "invoiceitem",
                "unit_amount_excluding_tax": "2000"
            }
        ],
        "has_more": False,
        "object": "list",
        "total_count": 1,
        "url": "/v1/invoices/in_1R3rNtHxQGFC458ofR7bp47B/lines"
    },
    "livemode": False,
    "metadata": {},
    "next_payment_attempt": None,
    "number": "468BD7A5-0001",
    "object": "invoice",
    "on_behalf_of": None,
    "paid": False,
    "paid_out_of_band": False,
    "payment_intent": "pi_3R3rNuHxQGFC458o1GyYd2p9",
    "payment_settings": {
        "default_mandate": None,
        "payment_method_options": None,
        "payment_method_types": None
    },
    "period_end": 1742270649,
    "period_start": 1742270649,
    "post_payment_credit_notes_amount": 0,
    "pre_payment_credit_notes_amount": 0,
    "quote": None,
    "receipt_number": None,
    "rendering": {
        "amount_tax_display": None,
        "pdf": {
            "page_size": "letter"
        },
        "template": None,
        "template_version": None
    },
    "shipping_cost": None,
    "shipping_details": None,
    "starting_balance": 0,
    "statement_descriptor": None,
    "status": "open",
    "status_transitions": {
        "finalized_at": 1742270650,
        "marked_uncollectible_at": None,
        "paid_at": None,
        "voided_at": None
    },
    "subscription": "sub_1R3fyyHxQGFC458oeUeioeEu",  # Using same subscription as success case
    "subscription_details": {
        "metadata": None
    },
    "subtotal": 2000,
    "subtotal_excluding_tax": 2000,
    "tax": None,
    "test_clock": None,
    "total": 2000,
    "total_discount_amounts": [],
    "total_excluding_tax": 2000,
    "total_pretax_credit_amounts": [],
    "total_tax_amounts": [],
    "transfer_data": None,
    "webhooks_delivered_at": 1742270650
}

# Create test event structure matching Stripe's webhook event format
INVOICE_PAYMENT_SUCCEEDED_EVENT = util.convert_to_stripe_object({
    'id': 'evt_test_invoice_payment_succeeded',
    'type': 'invoice.payment_succeeded',
    'data': {
        'object': _INVOICE_DATA
    }
}, stripe_account=None)

# Create test event for payment failed
INVOICE_PAYMENT_FAILED_EVENT = util.convert_to_stripe_object({
    'id': 'evt_test_invoice_payment_failed',
    'type': 'invoice.payment_failed',
    'data': {
        'object': _INVOICE_PAYMENT_FAILED_DATA
    }
}, stripe_account=None)
