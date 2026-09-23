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
