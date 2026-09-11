import { createHash } from "node:crypto";
import type { RuntimeMarket, RuntimeProduct } from "../../features/runtime/contracts.ts";

// Runtime business skill, deliberately independent of the developer's Codex Skills.
// The planner reads facts and selects this bounded operation; it has no transport access.
const definition = {
  id: "service.deliver-requested-product-card",
  version: "1.0.0",
  inputs: ["live request_card source event", "current exact product offer", "control and inbox revisions"],
  effect: "Freeze a localized text followed by the exact product card; never approve a sample or claim adoption.",
  authority: "An explicit new service request can be answered even when marketing is suppressed.",
};

export const CARD_SKILL = {
  ...definition,
  hash: createHash("sha256").update(JSON.stringify(definition)).digest("hex"),
};

export function planCard(market: RuntimeMarket, product: RuntimeProduct) {
  const text: Record<RuntimeMarket, string> = {
    mx: "Claro, te comparto la ficha del producto que solicitaste. Puedes revisar las condiciones en la tarjeta.",
    br: "Claro, vou compartilhar a ficha do produto que você pediu. Você pode conferir as condições no cartão.",
    it: "Certo, ti condivido la scheda del prodotto che hai richiesto. Puoi consultare le condizioni nella scheda.",
  };
  return [
    { kind: "text" as const, content: text[market] },
    { kind: "product_card" as const, content: JSON.stringify({
      simulator: true, productId: product.id, pid: product.pid, title: product.title,
      offerVersion: product.offerVersion, commissionBps: product.commissionBps,
      priceMinor: product.priceMinor, currency: product.currency, validUntil: product.validUntil,
    }) },
  ];
}
