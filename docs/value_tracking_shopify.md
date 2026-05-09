# Cómo activar Value Tracking en el Pixel de Shopify para Meta Ads

**Para:** Dev de Shopify de Serene
**De:** AI Performance Command Center
**Fecha:** 2026-05-09
**Bloquea:** Cálculo de ROAS en el dashboard de Serene · sección de profitability completa

---

## Por qué importa

El pixel actual de Meta envía el **evento Purchase** correctamente (lo confirmé con la API: 346 purchases registradas en mayo 2-9 en `act_1020250386264513`). Pero el evento **NO incluye el campo `value`** ni `currency`.

Sin esos dos campos, Meta Ads:

- ❌ No puede mostrar **ROAS** en reportes ni en la API
- ❌ No puede optimizar campañas por **Maximize Value** o **Min ROAS**
- ❌ No puede mostrar **purchase_value** ni **average order value (AOV)**

Con esos dos campos:

- ✅ ROAS = Revenue / Spend disponible en cada nivel (account, campaign, adset, ad)
- ✅ Optimización por valor (más rentable que por volumen en ecommerce)
- ✅ AOV por audiencia, por placement, por edad/género
- ✅ Sección de **profitability** completa en el dashboard AI

---

## Cómo verificar el estado actual

1. Ve a [Meta Events Manager](https://business.facebook.com/events_manager2)
2. Selecciona el pixel de Serene
3. **Test Events** tab
4. Hacer una compra real en sereneleparfum.com
5. Verás el evento `Purchase` aparecer

**Si en la columna "Value" dice `—` o está vacío** → estás como yo te describo. Hay que arreglar.

**Si en la columna "Value" muestra un número (ej. `120.00 USD`)** → ya está bien y el problema es otra cosa (en ese caso avísame).

---

## Cómo arreglarlo — 3 enfoques (elegir uno)

### Opción A — Customer Events de Shopify (recomendado, no toca theme)

Esta es la forma moderna y oficial de Shopify. **No requiere editar el theme.**

1. Shopify Admin → **Settings** → **Customer events**
2. Click en el pixel de Meta que ya está conectado (debería listarse como "Meta Pixel" o similar)
3. **Code editor** del custom pixel
4. Verifica que el evento de checkout completion tenga estos campos:

```javascript
// Ejemplo de cómo debe verse el evento Purchase del pixel
analytics.subscribe("checkout_completed", (event) => {
  const checkout = event.data.checkout;

  window.fbq('track', 'Purchase', {
    value: checkout.totalPrice.amount,           // ← CRÍTICO
    currency: checkout.totalPrice.currencyCode,  // ← CRÍTICO
    content_ids: checkout.lineItems.map(item => item.variant.id),
    content_type: 'product',
    num_items: checkout.lineItems.reduce((sum, item) => sum + item.quantity, 0),
    contents: checkout.lineItems.map(item => ({
      id: item.variant.id,
      quantity: item.quantity,
      item_price: item.variant.price.amount
    }))
  });
});
```

**Lo crítico es:** `value` y `currency` deben ser pasados como argumentos del segundo objeto.

5. **Save** y **Connect** el pixel
6. Hacer test purchase
7. Verificar en Test Events que ahora aparece el value

---

### Opción B — Shopify nativo (si usas Facebook & Instagram channel app)

Si tienes instalado el canal oficial **Facebook & Instagram by Meta**:

1. Apps → **Facebook & Instagram by Meta**
2. **Data sharing settings** → **Maximum** (no "Standard" ni "Enhanced")
3. **Conversions API** debe estar **enabled** (esto manda value server-side, doble redundancia)
4. **Customer information sharing** activado para mejor matching

**Maximum data sharing** automáticamente incluye `value` y `currency` en Purchase events. Es la forma más simple si no necesitas custom logic.

---

### Opción C — Edita theme.liquid manualmente (último recurso)

Solo si A y B no aplican. Buscar en `theme.liquid` o en el snippet del pixel un bloque tipo:

```liquid
{% if first_time_accessed %}
  {% if template contains 'thank_you' or template contains 'order' %}
    fbq('track', 'Purchase'); // ← le falta el segundo argumento
  {% endif %}
{% endif %}
```

Cambiarlo a:

```liquid
{% if first_time_accessed %}
  {% if template contains 'thank_you' or template contains 'order' %}
    fbq('track', 'Purchase', {
      value: {{ checkout.total_price | money_without_currency | replace: ',', '.' }},
      currency: '{{ shop.currency }}',
      content_ids: [{% for line_item in checkout.line_items %}'{{ line_item.variant.id }}'{% unless forloop.last %},{% endunless %}{% endfor %}],
      content_type: 'product',
      num_items: {{ checkout.item_count }}
    });
  {% endif %}
{% endif %}
```

⚠ **Cuidado con la moneda:** `total_price` en Shopify viene en *cents*, no en unidades. La división por 100 puede ser necesaria según el tema. Validar con un test purchase real.

---

## Cómo confirmar que funciona

Después de aplicar el fix:

1. Hacer compra de prueba real (puede ser un producto de $1 USD)
2. Esperar 1-2 minutos
3. Meta Events Manager → **Test Events** → ver el evento Purchase con `Value: 1.00 USD`
4. Avísame y yo verifico via API que `action_values` viene con datos
5. Re-genero el dashboard y la sección **ROAS** aparece

---

## Aviso a Conversions API (CAPI)

Si el pixel ya está conectado a Conversions API server-side (recomendado para iOS 14+):

- El value tracking debe estar tanto en el **client-side pixel** (script) **como** en CAPI (server-side)
- Si el cliente envía un valor pero el server no, Meta deduplicará y puede ignorar uno
- Verificar en **Events Manager** → **Diagnostics** que no haya warnings de "missing parameters"

---

## Tiempo estimado

- **Opción A**: 15-30 min (incluye test)
- **Opción B**: 5 min si ya está la app instalada
- **Opción C**: 30-60 min (test extenso para no romper nada)

**Recomendado:** Opción A. Es la forma actual de Shopify, no toca el theme, es testeable.

---

## Cuando esté hecho, avísame

Yo verifico desde la API que ahora viene `action_values` y `purchase_roas` no nulo, y el dashboard se actualiza automáticamente con la sección de ROAS y profitability.

— **Si necesitas ayuda con la implementación específica del Customer Event, mándame el código actual del pixel y te lo ajusto.**
