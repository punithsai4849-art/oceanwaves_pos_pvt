/* ═══════════════════════════════════════════════════════════════
   OCEANWAVES POS — billing.js  v4
   Dual pricing · PIN-based wholesale approval · High-volume optimised
═══════════════════════════════════════════════════════════════ */

// ── State ──────────────────────────────────────────────────────
let cart        = {};
let billType    = 'RETAIL';
let paymentMode = 'CASH';
let pinManagers = [];       // [{id, name}] loaded when AM modal opens
let pinSelected = null;     // currently selected manager id
let pinDigits   = [];       // current pin entry (array of chars)

// ── Bill type ──────────────────────────────────────────────────
function setBillType(type) {
  billType = type;
  const isWS = type === 'WHOLESALE';
  document.getElementById('btnWS').classList.toggle('active', isWS);
  document.getElementById('btnRetail').classList.toggle('active', !isWS);
  document.getElementById('wsForm').style.display  = isWS ? '' : 'none';
  
  // Recalc will handle showing/hiding CGST/SGST rows based on the actual toggle state
  recalc();

  const btnCredit = document.getElementById('btnCredit');
  if (btnCredit) {
      btnCredit.style.display = isWS ? 'flex' : 'none';
  }
  if (!isWS && paymentMode === 'CREDIT') {
      document.querySelector('.pm[data-m="CASH"]').click();
  }

  // Swap prices in cart
  Object.keys(cart).forEach(id => {
    cart[id].price = isWS ? cart[id].wsP : cart[id].retailP;
  });
  renderCart();
}

function updateGstLabels() {
  const rate = parseFloat(document.getElementById('gstRate')?.value || 18);
  const half = (rate / 2).toFixed(1);
  ['cgstLbl','sgstLbl'].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = half; });
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('gstRate')?.addEventListener('change', () => { updateGstLabels(); recalc(); });
  setBillType('RETAIL');
});

// ── Payment ────────────────────────────────────────────────────
function setPayment(mode, btn) {
  paymentMode = mode;
  document.querySelectorAll('.pm').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

// ── Cart ────────────────────────────────────────────────────────
function addToCart(tile) {
  if (tile.classList.contains('oos')) { toast('Out of stock!', 'error'); return; }
  const id = tile.dataset.id, name = tile.dataset.name;
  const retailP = parseFloat(tile.dataset.retail), wsP = parseFloat(tile.dataset.ws);
  const stock   = parseFloat(tile.dataset.stock);
  const price   = billType === 'WHOLESALE' ? wsP : retailP;
  if (cart[id]) {
    const nq = parseFloat((cart[id].qty + 0.5).toFixed(3));
    if (nq > stock) { toast(`Only ${stock} kg available!`, 'error'); return; }
    cart[id].qty = nq;
  } else {
    cart[id] = { name, qty: 1, price, retailP, wsP, stock };
  }
  renderCart();
  toast(`✓ ${name} added`, 'success');
}

function removeItem(id) { delete cart[id]; renderCart(); }
function clearCart() {
  if (!Object.keys(cart).length) return;
  if (confirm('Clear all items?')) { cart = {}; renderCart(); }
}
function updateQty(id, val) {
  const qty = parseFloat(val);
  if (isNaN(qty) || qty <= 0) { delete cart[id]; renderCart(); return; }
  if (qty > cart[id].stock) {
    toast(`Max ${cart[id].stock} kg`, 'error');
    cart[id].qty = cart[id].stock;
    document.querySelector(`input.qty-in[data-id="${id}"]`).value = cart[id].stock;
  } else { cart[id].qty = qty; }
  recalc();
}
function updatePrice(id, val) {
  const p = parseFloat(val);
  if (!isNaN(p) && p >= 0) {
    cart[id].price = p;
    // Also update the base price for current bill type so switching doesn't reset
    if (billType === 'WHOLESALE') cart[id].wsP = p;
    else cart[id].retailP = p;
  }
  recalc();
}

function renderCart() {
  const body = document.getElementById('cartBody');
  const keys = Object.keys(cart);
  if (!keys.length) {
    body.innerHTML = `<tr id="emptyRow"><td colspan="5" class="empty-cart">
      <i class="fas fa-shopping-cart"></i><br>Click products to add</td></tr>`;
    recalc(); return;
  }
  body.innerHTML = keys.map(id => {
    const it    = cart[id];
    const total = (it.qty * it.price).toFixed(2);
    return `<tr>
      <td><div class="ci-name">${esc(it.name)}</div></td>
      <td><input type="number" class="qty-in" data-id="${id}"
            value="${it.qty}" min="0.001" max="${it.stock}" step="0.5"
            oninput="updateQty('${id}',this.value)"></td>
      <td>
        <div class="price-edit-wrap">
          ${CAN_EDIT_PRICE ? '<span class="price-edit-icon">✏️</span>' : ''}
          <input type="number" class="price-in ${CAN_EDIT_PRICE ? 'editable' : ''}" data-id="${id}"
                value="${it.price}" min="0" step="0.5"
                oninput="updatePrice('${id}',this.value)"
                ${CAN_EDIT_PRICE ? '' : 'readonly'}
                title="${CAN_EDIT_PRICE ? 'Tap to edit price' : 'Price is fixed'}">
        </div>
      </td>
      <td class="ci-total">₹${total}</td>
      <td><button class="btn-rm" onclick="removeItem('${id}')">
            <i class="fas fa-times"></i></button></td>
    </tr>`;
  }).join('');
  recalc();
}

function recalc() {
  let sub = 0;
  Object.values(cart).forEach(it => sub += it.qty * it.price);
  const disc = parseFloat(document.getElementById('discountAmt')?.value || 0) || 0;
  const discRow = document.getElementById('discRow'), discVal = document.getElementById('discVal');
  if (disc > 0) { discRow.style.display = ''; discVal.textContent = `-₹${disc.toFixed(2)}`; }
  else            { discRow.style.display = 'none'; }
  const afterD = Math.max(0, sub - disc);
  document.getElementById('subtotalVal').textContent = `₹${sub.toFixed(2)}`;

  // GST — read toggle state
  const gstOn   = document.getElementById('gstToggle')?.checked;
  const gstRate = gstOn ? parseFloat(document.getElementById('gstRate')?.value || 18) : 0;
  const half    = gstRate / 2;
  const cgst    = gstOn ? (afterD * half / 100) : 0;
  const sgst    = cgst;

  const cgstRow = document.getElementById('cgstRow');
  const sgstRow = document.getElementById('sgstRow');
  if (gstOn && gstRate > 0) {
    cgstRow.style.display = '';
    sgstRow.style.display = '';
    document.getElementById('cgstLbl').textContent = half.toFixed(1);
    document.getElementById('sgstLbl').textContent = half.toFixed(1);
    document.getElementById('cgstVal').textContent = `₹${cgst.toFixed(2)}`;
    document.getElementById('sgstVal').textContent = `₹${sgst.toFixed(2)}`;
  } else {
    cgstRow.style.display = 'none';
    sgstRow.style.display = 'none';
  }

  const grand = afterD + cgst + sgst;
  document.getElementById('grandVal').textContent = `₹${grand.toFixed(2)}`;
}

// ── Update product tile stock in DOM after a successful sale ──────────────
function updateStockInUI(soldItems) {
  soldItems.forEach(({ product_id, quantity }) => {
    const tile = document.querySelector(`.prod-tile[data-id="${product_id}"]`);
    if (!tile) return;

    const newStock  = Math.max(0, parseFloat(tile.dataset.stock) - quantity);
    const lowAlert  = parseFloat(tile.dataset.lowAlert || 0);
    tile.dataset.stock = newStock;

    // Update displayed stock text
    const stockEl = tile.querySelector('.pt-stock');
    if (stockEl) {
      stockEl.textContent = `${newStock} kg`;
      stockEl.classList.toggle('low', newStock > 0 && newStock <= lowAlert);
    }

    // Remove old badge
    tile.querySelector('.pt-badge')?.remove();

    // Update tile classes and re-add badge
    tile.classList.remove('oos', 'low');
    if (newStock <= 0) {
      tile.classList.add('oos');
      const b = document.createElement('div');
      b.className = 'pt-badge oos-badge'; b.textContent = 'Out';
      tile.appendChild(b);
    } else if (newStock <= lowAlert) {
      tile.classList.add('low');
      const b = document.createElement('div');
      b.className = 'pt-badge low-badge'; b.textContent = 'Low';
      tile.appendChild(b);
    }
  });
}

// ── Save entry point ───────────────────────────────────────────
// Store the pending action so the PIN modal can use it too
let pendingBillAction = 'print';

async function saveBill(action = 'print') {
  pendingBillAction = action;
  if (!Object.keys(cart).length) { toast('Add items first!', 'error'); return; }
  if (billType === 'RETAIL') {
    // Retail: direct save, no approval needed
    const btns = document.querySelectorAll('.btn-save');
    btns.forEach(b => { b.disabled = true; });
    const r = await _post(SAVE_URL);
    btns.forEach(b => { b.disabled = false; });
    if (r.success) {
      toast(`✓ Bill #${r.bill_number} saved!`, 'success');
      // Snapshot cart BEFORE resetBill() clears it
      const soldItems = Object.entries(cart).map(([id, it]) => ({ product_id: parseInt(id), quantity: it.qty }));
      if (action === 'whatsapp' && r.whatsapp_url) {
        window.open(r.whatsapp_url, '_blank');
      } else {
        window.open(PRINT_BASE + r.bill_id + '/', '_blank');
      }
      updateStockInUI(soldItems);
      resetBill();
    } else { toast('❌ ' + r.error, 'error'); }
  } else {
    // Wholesale: validate then open PIN modal
    if (!document.getElementById('custName')?.value?.trim()) {
      toast('Customer name required for wholesale!', 'error');
      document.getElementById('custName').focus(); return;
    }
    openPinModal();
  }
}

function getBillPayload(extra = {}) {
  const gstOn   = document.getElementById('gstToggle')?.checked;
  const gstRate = gstOn ? parseFloat(document.getElementById('gstRate')?.value || 18) : 0;
  return {
    items: Object.entries(cart).map(([id, it]) => ({
      product_id: parseInt(id), quantity: it.qty, selling_price: it.price,
    })),
    bill_type:      billType,
    payment_mode:   paymentMode,
    gst_rate:       gstRate,
    discount:       parseFloat(document.getElementById('discountAmt')?.value || 0) || 0,
    customer_name:    document.getElementById('custName')?.value?.trim()    || '',
    customer_phone:   document.getElementById('custPhone')?.value?.trim()   || '',
    customer_gst:     document.getElementById('custGST')?.value?.trim()     || '',
    customer_address: document.getElementById('custAddress')?.value?.trim() || '',
    ...extra,
  };
}

async function _post(url, payload = null) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(payload !== null ? payload : getBillPayload()),
    });
    return await res.json();
  } catch (e) { return { success: false, error: 'Network error.' }; }
}

function resetBill() {
  cart = {}; renderCart();
  if (document.getElementById('discountAmt')) document.getElementById('discountAmt').value = 0;
  ['custName','custPhone','custGST'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
}

// ══════════════════════════════════════════════════════════════
//  PIN APPROVAL MODAL
// ══════════════════════════════════════════════════════════════

function showPinStep(id) {
  ['pinStepLoading','pinStepForm','pinStepError'].forEach(s => {
    document.getElementById(s).style.display = s === id ? '' : 'none';
  });
}

function buildBillStrip() {
  const items   = Object.values(cart);
  const sub     = items.reduce((s, i) => s + i.qty * i.price, 0);
  const disc    = parseFloat(document.getElementById('discountAmt')?.value || 0) || 0;
  
  const gstOn   = document.getElementById('gstToggle')?.checked;
  const rate    = gstOn ? parseFloat(document.getElementById('gstRate')?.value || 18) : 0;
  
  const afterD  = sub - disc;
  const grand   = afterD + (afterD * rate / 100);
  const summary = items.slice(0, 3).map(i => `${i.name} ${i.qty}kg`).join(', ')
                + (items.length > 3 ? ` +${items.length - 3} more` : '');
  return `<div class="pbs-inner">
    <div class="pbs-items"><i class="fas fa-fish"></i> ${summary}</div>
    <div class="pbs-amount">₹${grand.toFixed(2)}<span>Grand Total</span></div>
  </div>`;
}

async function openPinModal() {
  pinSelected = null; pinDigits = [];
  clearPinBoxes(); setPinFeedback('', '');
  document.getElementById('pinModal').classList.add('active');
  document.getElementById('pinBillStrip').innerHTML = buildBillStrip();
  document.getElementById('pinSubtitle').textContent = 'Select Manager & Request OTP';
  
  showPinStep('pinStepLoading');
  
  document.getElementById('otpInputSection').style.display = 'none';
  document.getElementById('btnRequestOTP').innerHTML = '<i class="fas fa-paper-plane"></i> Send OTP to Manager';
  loadManagers();
}

async function loadManagers() {
  try {
    const res  = await fetch(WS_MANAGERS_URL, { headers: { 'X-CSRFToken': CSRF } });
    const data = await res.json();

    if (!data.success) {
      document.getElementById('pinErrMsg').innerHTML =
        `<i class="fas fa-exclamation-circle"></i> ${data.error}`;
      showPinStep('pinStepError'); return;
    }

    pinManagers = data.managers;

    // Build manager selector
    const wrap = document.getElementById('managerSelectWrap');
    const list = document.getElementById('managerList');

    if (pinManagers.length === 0) {
      document.getElementById('pinErrMsg').innerHTML =
        `<i class="fas fa-exclamation-circle"></i> No Area Manager has an Email set. ` +
        `Go to Area Managers page and set Emails first.`;
      showPinStep('pinStepError'); return;
    }

    if (pinManagers.length === 1) {
      // Only one AM — auto-select, hide picker
      pinSelected = pinManagers[0].id;
      document.getElementById('pinLabel').textContent =
        `Enter PIN for ${pinManagers[0].name}:`;
      wrap.style.display = 'none';
    } else {
      // Multiple AMs — show selector
      list.innerHTML = pinManagers.map(m =>
        `<button class="mgr-btn" data-id="${m.id}" onclick="selectManager(${m.id}, '${esc(m.name)}', this)">
          <i class="fas fa-user-shield"></i> ${esc(m.name)}
        </button>`
      ).join('');
      wrap.style.display = '';
      document.getElementById('pinLabel').textContent = 'Enter PIN:';
    }

    // Warn about AMs without Emails
    if (data.managers_no_pin && data.managers_no_pin.length > 0) {
      setPinFeedback(`⚠ No Email configured for: ${data.managers_no_pin.join(', ')}`, 'warn');
    }

    showPinStep('pinStepForm');

    // Auto-focus first PIN box
    setTimeout(() => document.querySelector('.pin-box')?.focus(), 100);

  } catch (e) {
    document.getElementById('pinErrMsg').innerHTML =
      `<i class="fas fa-exclamation-circle"></i> Network error. Check connection.`;
    showPinStep('pinStepError');
  }
}

function selectManager(id, name, btn) {
  pinSelected = id;
  document.querySelectorAll('.mgr-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('pinLabel').textContent = `Enter PIN for ${name}:`;
  clearPinBoxes();
  document.querySelector('.pin-box')?.focus();
}

function closePinModal() {
  document.getElementById('pinModal').classList.remove('active');
  pinDigits = []; pinSelected = null;
}

// ── Digit box helpers ──────────────────────────────────────────
function clearPinBoxes() {
  pinDigits = [];
  document.querySelectorAll('.pin-box').forEach(b => {
    b.value = ''; b.classList.remove('filled', 'pin-error');
  });
}

function updateBoxesFromDigits() {
  const boxes = document.querySelectorAll('.pin-box');
  boxes.forEach((b, i) => {
    b.value = pinDigits[i] ? '•' : '';
    b.classList.toggle('filled', !!pinDigits[i]);
  });
}

// Keyboard input on boxes
function pinBoxInput(el, idx) {
  const val = el.value.replace(/\D/g, '');
  if (val) {
    pinDigits[idx] = val.slice(-1);
    updateBoxesFromDigits();
    // Advance focus
    const next = document.querySelectorAll('.pin-box')[idx + 1];
    if (next) next.focus();
    // Auto-verify at 6 digits OR when on last required box (4) and no optional ones filled
    checkAutoVerify();
  } else {
    pinDigits[idx] = undefined;
    updateBoxesFromDigits();
  }
}

function pinBoxKey(event, idx) {
  if (event.key === 'Backspace') {
    const boxes = document.querySelectorAll('.pin-box');
    if (!pinDigits[idx] && idx > 0) {
      pinDigits[idx - 1] = undefined;
      updateBoxesFromDigits();
      boxes[idx - 1].focus();
    } else {
      pinDigits[idx] = undefined;
      updateBoxesFromDigits();
    }
    event.preventDefault();
  }
  if (event.key === 'Enter') verifyPin();
}

// Numeric keypad (touch)
function npPress(d) {
  if (pinDigits.filter(Boolean).length >= 6) return;
  const idx = pinDigits.findIndex(v => !v);
  const target = idx === -1 ? pinDigits.length : idx;
  if (target >= 6) return;
  pinDigits[target] = d;
  updateBoxesFromDigits();
  checkAutoVerify();
}
function npBackspace() {
  for (let i = 5; i >= 0; i--) {
    if (pinDigits[i]) { pinDigits[i] = undefined; updateBoxesFromDigits(); break; }
  }
}
function npClear() { clearPinBoxes(); }

function checkAutoVerify() {
  const entered = pinDigits.filter(Boolean).join('');
  // Auto-verify if 6 digits entered
  if (entered.length === 6) { setTimeout(verifyPin, 120); }
}

async function requestOTP() {
  if (!pinSelected) {
    setPinFeedback('Please select a manager first.', 'error'); return;
  }
  const btn = document.getElementById('btnRequestOTP');
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending OTP...';
  setPinFeedback('', '');
  
  try {
    const res = await fetch(WS_REQUEST_OTP, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ manager_id: pinSelected })
    });
    const data = await res.json();
    if (data.success) {
      setPinFeedback('OTP Sent to Manager Email!', 'success');
      document.getElementById('otpInputSection').style.display = 'block';
      setTimeout(() => document.querySelector('.pin-box').focus(), 100);
    } else {
      setPinFeedback(data.error, 'error');
    }
  } catch (e) {
    setPinFeedback('Network error. Try again.', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-paper-plane"></i> Resend OTP';
  }
}

// ── Verify OTP ─────────────────────────────────────────────────
async function verifyPin() {
  if (!pinSelected) {
    setPinFeedback('Please select a manager first.', 'error'); return;
  }
  const entered = pinDigits.filter(Boolean).join('');
  if (entered.length < 6) {
    setPinFeedback('OTP must be exactly 6 digits.', 'error'); return;
  }

  const btn = document.getElementById('btnVerifyPin');
  btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Verifying...';
  setPinFeedback('', '');

  const payload = getBillPayload({ manager_id: pinSelected, pin: entered });

  try {
    const res  = await fetch(WS_VERIFY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (data.success) {
      // Flash success
      document.querySelectorAll('.pin-box').forEach(b => b.classList.add('filled'));
      setPinFeedback(`✓ Approved by ${data.approved_by}!`, 'success');
      // Snapshot cart BEFORE the timeout clears it via resetBill()
      const soldItems = Object.entries(cart).map(([id, it]) => ({ product_id: parseInt(id), quantity: it.qty }));
      setTimeout(() => {
        closePinModal();
        toast(`✓ Bill #${data.bill_number} approved by ${data.approved_by}!`, 'success');
        if (pendingBillAction === 'whatsapp' && data.whatsapp_url) {
          window.open(data.whatsapp_url, '_blank');
        } else {
          window.open(PRINT_BASE + data.bill_id + '/', '_blank');
        }
        updateStockInUI(soldItems);
        resetBill();
      }, 700);

    } else if (data.locked) {
      document.getElementById('pinErrMsg').innerHTML =
        `<i class="fas fa-lock"></i> ${data.error}`;
      showPinStep('pinStepError');

    } else {
      // Wrong PIN — shake boxes, clear, re-focus
      document.querySelectorAll('.pin-box').forEach(b => b.classList.add('pin-error'));
      setTimeout(() => {
        document.querySelectorAll('.pin-box').forEach(b => b.classList.remove('pin-error'));
        clearPinBoxes();
        document.querySelector('.pin-box')?.focus();
      }, 500);
      setPinFeedback(data.error, 'error');
    }

  } catch (e) {
    setPinFeedback('Network error. Try again.', 'error');
  } finally {
    btn.disabled = false; btn.innerHTML = '<i class="fas fa-check-circle"></i> Verify & Save Bill';
  }
}

function setPinFeedback(msg, type) {
  const el = document.getElementById('pinFeedback');
  el.textContent = msg; el.className = `pin-feedback ${type}`;
}

// ── Product filter ─────────────────────────────────────────────
function filterProds() {
  const q = document.getElementById('prodSearch').value.toLowerCase();
  document.querySelectorAll('.prod-tile').forEach(t => {
    t.style.display = (t.dataset.name.toLowerCase().includes(q) ||
                       t.dataset.cat.toLowerCase().includes(q)) ? '' : 'none';
  });
}
document.querySelectorAll('.cf').forEach(btn => {
  btn.addEventListener('click', function () {
    document.querySelectorAll('.cf').forEach(b => b.classList.remove('active'));
    this.classList.add('active');
    const cat = this.dataset.cat;
    document.querySelectorAll('.prod-tile').forEach(t => {
      t.style.display = (cat === 'ALL' || t.dataset.cat === cat) ? '' : 'none';
    });
  });
});

// ── Toast ──────────────────────────────────────────────────────
function toast(msg, type = 'success') {
  document.querySelector('.pos-toast')?.remove();
  const el = document.createElement('div');
  el.className = `pos-toast ${type}`; el.innerHTML = msg;
  document.body.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .4s';
    setTimeout(() => el.remove(), 400); }, 2800);
}
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('pinModal')?.addEventListener('click', e => {
    if (e.target.id === 'pinModal') closePinModal();
  });
});
