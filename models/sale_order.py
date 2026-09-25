from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _action_cancel(self):
        """Cancel linked Tamara payments on Tamara before canceling the sales order."""
        for order in self:
            order._tamara_cancel_linked_payments()
        return super()._action_cancel()

    def _tamara_cancel_linked_payments(self):
        """Ask Tamara to cancel each linked Tamara payment for this sales order.

        :return: None
        """
        self.ensure_one()
        txs = self.transaction_ids.filtered(
            lambda tx: (
                tx.provider_code == 'tamara'
                and (tx.tamara_order_id or tx.provider_reference)
                and tx.state not in ('draft', 'cancel', 'error')
            )
        )
        for tx in txs:
            tx._tamara_cancel_from_sale_order(self)

    def _create_invoices(self, grouped=False, final=False, date=None):
        """Create invoices, then fully capture Tamara orders when configured."""
        previous = {order.id: order.invoice_status for order in self}
        invoices = super()._create_invoices(grouped=grouped, final=final, date=date)
        for order in self:
            if order.invoice_status == 'invoiced' and previous.get(order.id) != 'invoiced':
                order._tamara_maybe_capture_on_trigger('fully_invoice')
        return invoices

    def _tamara_maybe_capture_on_trigger(self, trigger):
        """Fully capture linked Tamara payments when the configured trigger matches.

        :param str trigger: `fully_invoice` or `fully_delivered`.
        :return: None
        """
        self.ensure_one()
        txs = self.transaction_ids.filtered(
            lambda tx: (
                tx.provider_code == 'tamara'
                and tx.provider_id.tamara_capture_trigger == trigger
                and (tx.tamara_order_id or tx.provider_reference)
                and tx.state not in ('draft', 'cancel', 'error')
                and (tx.tamara_order_status or '').lower() not in {
                    'fully_captured', 'captured',
                }
            )
        )
        for tx in txs:
            tx._tamara_capture_from_sale_order(self)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        """Mark pickings done, then capture Tamara orders when the SO is fully delivered."""
        res = super()._action_done()
        orders = self.mapped('sale_id').filtered(lambda order: order.delivery_status == 'full')
        for order in orders:
            order._tamara_maybe_capture_on_trigger('fully_delivered')
        return res
