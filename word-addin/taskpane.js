/**
 * LawHand - Word Add-in Task Pane
 * Vanilla JS implementation for Office.js
 */

'use strict';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

var API_BASE = 'http://localhost:8000/api';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

var state = {
  token: null,
  user: null,
  conversations: [],
  activeConvId: null,
  messages: [],
  documents: [],
  lastAssistantContent: null,
  isSending: false,
};

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function $(id) {
  return document.getElementById(id);
}

function show(id) {
  var el = $(id);
  if (el) el.classList.remove('hidden');
}

function hide(id) {
  var el = $(id);
  if (el) el.classList.add('hidden');
}

function setStatus(msg, isError) {
  var el = $('status-text');
  if (!el) return;
  el.textContent = msg;
  el.style.color = isError ? '#dc2626' : '#6b7280';
}

// ---------------------------------------------------------------------------
// Token / auth storage
// ---------------------------------------------------------------------------

function loadToken() {
  state.token = localStorage.getItem('ls_addin_token');
}

function saveToken(token) {
  state.token = token;
  localStorage.setItem('ls_addin_token', token);
}

function clearToken() {
  state.token = null;
  state.user = null;
  localStorage.removeItem('ls_addin_token');
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

function apiFetch(path, options) {
  var url = API_BASE + path;
  var headers = { 'Content-Type': 'application/json' };
  if (state.token) {
    headers['Authorization'] = 'Bearer ' + state.token;
  }
  var opts = Object.assign({ headers: headers }, options || {});
  if (opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
  }
  if (opts.body instanceof FormData) {
    delete opts.headers['Content-Type'];
  }
  return fetch(url, opts).then(function (res) {
    if (res.status === 401) {
      clearToken();
      showLoginSection();
      throw new Error('Unauthorized');
    }
    if (!res.ok) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        throw new Error(data.detail || ('HTTP error ' + res.status));
      });
    }
    return res.json().catch(function () { return {}; });
  });
}

// ---------------------------------------------------------------------------
// Office.js helpers
// ---------------------------------------------------------------------------

function getSelectedText(callback) {
  if (!Office || !Office.context || !Office.context.document) {
    callback(null, 'Office context not available');
    return;
  }
  Office.context.document.getSelectedDataAsync(
    Office.CoercionType.Text,
    function (result) {
      if (result.status === Office.AsyncResultStatus.Succeeded) {
        callback(result.value, null);
      } else {
        callback(null, result.error ? result.error.message : 'Could not get selection');
      }
    }
  );
}

function insertTextAtCursor(text, callback) {
  if (!Office || !Office.context || !Office.context.document) {
    if (callback) callback('Office context not available');
    return;
  }
  Office.context.document.setSelectedDataAsync(
    text,
    { coercionType: Office.CoercionType.Text },
    function (result) {
      if (result.status === Office.AsyncResultStatus.Succeeded) {
        if (callback) callback(null);
      } else {
        if (callback) callback(result.error ? result.error.message : 'Insert failed');
      }
    }
  );
}

// ---------------------------------------------------------------------------
// UI: Login
// ---------------------------------------------------------------------------

function showLoginSection() {
  show('login-section');
  hide('chat-section');
  hide('docs-section');
  hide('btn-logout');
}

function showChatSection() {
  hide('login-section');
  show('chat-section');
  show('docs-section');
  show('btn-logout');
}

// ---------------------------------------------------------------------------
// UI: Messages
// ---------------------------------------------------------------------------

function renderMessages() {
  var list = $('messages-list');
  if (!list) return;

  if (state.messages.length === 0) {
    list.innerHTML = '<div id="messages-empty"><p>Ask a legal question or use "Get Selected Text" to analyze document text.</p></div>';
    hide('insert-btn-wrapper');
    return;
  }

  var html = '';
  for (var i = 0; i < state.messages.length; i++) {
    var msg = state.messages[i];
    html += buildMessageHTML(msg);
  }
  list.innerHTML = html;

  // Show insert button if last message is assistant
  var lastMsg = state.messages[state.messages.length - 1];
  if (lastMsg && lastMsg.role === 'assistant') {
    state.lastAssistantContent = lastMsg.content;
    show('insert-btn-wrapper');
  } else {
    hide('insert-btn-wrapper');
  }

  // Scroll to bottom
  list.scrollTop = list.scrollHeight;
}

function buildMessageHTML(msg) {
  var isUser = msg.role === 'user';
  var contentEscaped = escapeHTML(msg.content || '');
  var sourcesHTML = '';

  if (msg.sources && msg.sources.length > 0) {
    var sourceItems = '';
    for (var j = 0; j < msg.sources.length; j++) {
      var src = msg.sources[j];
      sourceItems += '<div class="citation-card">';
      sourceItems += '<div class="citation-name">' + escapeHTML(src.case_name || '') + '</div>';
      if (src.citation) {
        sourceItems += '<div class="citation-ref">' + escapeHTML(src.citation) + '</div>';
      }
      if (src.court) {
        sourceItems += '<div class="citation-court">' + escapeHTML(src.court) + '</div>';
      }
      if (src.excerpt) {
        sourceItems += '<div class="citation-excerpt">' + escapeHTML(src.excerpt) + '</div>';
      }
      sourceItems += '</div>';
    }
    sourcesHTML = '<div class="sources-section"><button class="sources-toggle" onclick="toggleSources(this)">&#9658; ' + msg.sources.length + ' Source(s)</button><div class="sources-list hidden">' + sourceItems + '</div></div>';
  }

  return '<div class="message ' + (isUser ? 'message-user' : 'message-assistant') + '">'
    + '<div class="message-content">' + contentEscaped + '</div>'
    + sourcesHTML
    + '</div>';
}

function toggleSources(btn) {
  var list = btn.nextElementSibling;
  if (!list) return;
  if (list.classList.contains('hidden')) {
    list.classList.remove('hidden');
    btn.innerHTML = btn.innerHTML.replace('&#9658;', '&#9660;');
  } else {
    list.classList.add('hidden');
    btn.innerHTML = btn.innerHTML.replace('&#9660;', '&#9658;');
  }
}

// Make toggleSources globally accessible
window.toggleSources = toggleSources;

function addTypingIndicator() {
  var list = $('messages-list');
  if (!list) return;
  var div = document.createElement('div');
  div.id = 'typing-indicator';
  div.className = 'message message-assistant';
  div.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
  list.appendChild(div);
  list.scrollTop = list.scrollHeight;
}

function removeTypingIndicator() {
  var el = $('typing-indicator');
  if (el) el.parentNode.removeChild(el);
}

// ---------------------------------------------------------------------------
// UI: Documents
// ---------------------------------------------------------------------------

function renderDocuments() {
  var list = $('docs-list');
  if (!list) return;

  if (state.documents.length === 0) {
    list.innerHTML = '<p id="docs-empty">No documents uploaded.</p>';
    return;
  }

  var html = '';
  for (var i = 0; i < state.documents.length; i++) {
    var doc = state.documents[i];
    var statusClass = 'status-' + (doc.status || 'unknown');
    html += '<div class="doc-item">'
      + '<span class="doc-name">' + escapeHTML(doc.filename || 'Unknown') + '</span>'
      + '<span class="doc-status ' + statusClass + '">' + escapeHTML(doc.status || '') + '</span>'
      + '</div>';
  }
  list.innerHTML = html;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

function fetchMe() {
  return apiFetch('/auth/me').then(function (user) {
    state.user = user;
    return user;
  });
}

function fetchConversations() {
  return apiFetch('/conversations').then(function (convs) {
    state.conversations = convs || [];
    updateConvTitle();
    return convs;
  });
}

function fetchDocuments() {
  return apiFetch('/documents').then(function (docs) {
    state.documents = docs || [];
    renderDocuments();
    return docs;
  });
}

function loadConversation(id) {
  state.activeConvId = id;
  return apiFetch('/conversations/' + id).then(function (data) {
    state.messages = data.messages || [];
    var title = (data.conversation && data.conversation.title) ? data.conversation.title : 'Conversation';
    var el = $('conv-title');
    if (el) el.textContent = title;
    renderMessages();
  });
}

function createNewConversation() {
  return apiFetch('/conversations', { method: 'POST', body: {} }).then(function (conv) {
    state.conversations.unshift(conv);
    state.activeConvId = conv.id;
    state.messages = [];
    var el = $('conv-title');
    if (el) el.textContent = conv.title || 'New Conversation';
    renderMessages();
    return conv;
  });
}

function doSendMessage(content) {
  if (state.isSending) return;
  if (!content || !content.trim()) {
    setStatus('Please enter a message.', true);
    return;
  }

  var convId = state.activeConvId;

  var proceed = function (cid) {
    state.isSending = true;
    var input = $('chat-input');
    if (input) input.disabled = true;
    var sendBtn = $('btn-send');
    if (sendBtn) sendBtn.disabled = true;

    var userMsg = {
      id: 'temp-' + Date.now(),
      role: 'user',
      content: content,
      sources: [],
      created_at: new Date().toISOString(),
    };
    state.messages.push(userMsg);
    renderMessages();
    addTypingIndicator();

    apiFetch('/conversations/' + cid + '/messages', {
      method: 'POST',
      body: {
        content: content,
        include_public: true,
        use_premium_llm: false,
      },
    })
      .then(function (msg) {
        removeTypingIndicator();
        state.messages.push(msg);
        renderMessages();
        setStatus('');
      })
      .catch(function (err) {
        removeTypingIndicator();
        state.messages.push({
          id: 'err-' + Date.now(),
          role: 'assistant',
          content: 'Error: ' + (err.message || 'Could not get response.'),
          sources: [],
          created_at: new Date().toISOString(),
        });
        renderMessages();
        setStatus(err.message || 'Send failed', true);
      })
      .finally(function () {
        state.isSending = false;
        if (input) {
          input.disabled = false;
          input.value = '';
          input.focus();
        }
        if (sendBtn) sendBtn.disabled = false;
      });
  };

  if (!convId) {
    createNewConversation()
      .then(function (conv) { proceed(conv.id); })
      .catch(function (err) {
        setStatus('Could not create conversation: ' + err.message, true);
      });
  } else {
    proceed(convId);
  }
}

function updateConvTitle() {
  if (state.activeConvId) {
    for (var i = 0; i < state.conversations.length; i++) {
      if (state.conversations[i].id === state.activeConvId) {
        var el = $('conv-title');
        if (el) el.textContent = state.conversations[i].title || 'Conversation';
        break;
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Auth flow
// ---------------------------------------------------------------------------

function openAuthPopup(url) {
  var width = 500;
  var height = 600;
  var left = (screen.width - width) / 2;
  var top = (screen.height - height) / 2;
  var popup = window.open(
    url,
    'LawHandAuth',
    'width=' + width + ',height=' + height + ',left=' + left + ',top=' + top
  );

  if (!popup) {
    setStatus('Popup blocked. Please allow popups for this add-in.', true);
    return;
  }

  var pollTimer = setInterval(function () {
    try {
      if (popup.closed) {
        clearInterval(pollTimer);
        return;
      }
      var href = popup.location.href;
      if (href && href.indexOf('token=') !== -1) {
        clearInterval(pollTimer);
        popup.close();
        var tokenMatch = href.match(/[?&]token=([^&]+)/);
        if (tokenMatch) {
          var token = decodeURIComponent(tokenMatch[1]);
          saveToken(token);
          onTokenReceived();
        } else {
          setStatus('Authentication failed: no token in response.', true);
        }
      }
    } catch (e) {
      // Cross-origin — keep waiting
    }
  }, 500);
}

function onTokenReceived() {
  setStatus('Signing in...');
  fetchMe()
    .then(function () {
      return Promise.all([fetchConversations(), fetchDocuments()]);
    })
    .then(function (results) {
      var convs = results[0];
      showChatSection();
      if (convs && convs.length > 0) {
        return loadConversation(convs[0].id);
      }
    })
    .then(function () {
      setStatus('');
    })
    .catch(function (err) {
      setStatus('Sign-in failed: ' + (err.message || 'Unknown error'), true);
      clearToken();
      showLoginSection();
    });
}

// ---------------------------------------------------------------------------
// Escape HTML
// ---------------------------------------------------------------------------

function escapeHTML(str) {
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

function wireEvents() {
  // Microsoft login
  var btnMS = $('btn-ms-login');
  if (btnMS) {
    btnMS.addEventListener('click', function () {
      openAuthPopup(API_BASE + '/auth/microsoft/login');
    });
  }

  // Google login
  var btnGoogle = $('btn-google-login');
  if (btnGoogle) {
    btnGoogle.addEventListener('click', function () {
      openAuthPopup(API_BASE + '/auth/google/login');
    });
  }

  // Logout
  var btnLogout = $('btn-logout');
  if (btnLogout) {
    btnLogout.addEventListener('click', function () {
      clearToken();
      state.conversations = [];
      state.activeConvId = null;
      state.messages = [];
      state.documents = [];
      showLoginSection();
      setStatus('');
    });
  }

  // New conversation
  var btnNewConv = $('btn-new-conv');
  if (btnNewConv) {
    btnNewConv.addEventListener('click', function () {
      createNewConversation().catch(function (err) {
        setStatus('Could not create conversation: ' + err.message, true);
      });
    });
  }

  // Send message
  var btnSend = $('btn-send');
  if (btnSend) {
    btnSend.addEventListener('click', function () {
      var input = $('chat-input');
      if (input) doSendMessage(input.value);
    });
  }

  // Enter to send (Shift+Enter = newline)
  var chatInput = $('chat-input');
  if (chatInput) {
    chatInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        doSendMessage(chatInput.value);
      }
    });
  }

  // Get selected text
  var btnGetSel = $('btn-get-selection');
  if (btnGetSel) {
    btnGetSel.addEventListener('click', function () {
      getSelectedText(function (text, err) {
        if (err) {
          setStatus('Could not get selection: ' + err, true);
          return;
        }
        var input = $('chat-input');
        if (input) {
          var existing = input.value.trim();
          input.value = existing
            ? existing + '\n\n---\nSelected text:\n' + text
            : 'Regarding the following text:\n\n"' + text + '"\n\n';
          input.focus();
        }
        setStatus('Text inserted into input field.');
      });
    });
  }

  // Insert draft at cursor
  var btnInsert = $('btn-insert-draft');
  if (btnInsert) {
    btnInsert.addEventListener('click', function () {
      if (!state.lastAssistantContent) {
        setStatus('No draft to insert.', true);
        return;
      }
      insertTextAtCursor(state.lastAssistantContent, function (err) {
        if (err) {
          setStatus('Insert failed: ' + err, true);
        } else {
          setStatus('Draft inserted at cursor.');
        }
      });
    });
  }

  // Toggle docs
  var btnToggleDocs = $('btn-toggle-docs');
  if (btnToggleDocs) {
    btnToggleDocs.addEventListener('click', function () {
      var docsList = $('docs-list');
      if (!docsList) return;
      if (docsList.classList.contains('hidden')) {
        docsList.classList.remove('hidden');
        btnToggleDocs.textContent = 'Hide';
      } else {
        docsList.classList.add('hidden');
        btnToggleDocs.textContent = 'Show';
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function init() {
  loadToken();
  wireEvents();

  if (state.token) {
    setStatus('Restoring session...');
    fetchMe()
      .then(function () {
        return Promise.all([fetchConversations(), fetchDocuments()]);
      })
      .then(function (results) {
        var convs = results[0];
        showChatSection();
        if (convs && convs.length > 0) {
          return loadConversation(convs[0].id);
        }
      })
      .then(function () {
        setStatus('');
      })
      .catch(function () {
        clearToken();
        showLoginSection();
        setStatus('');
      });
  } else {
    showLoginSection();
  }
}

// ---------------------------------------------------------------------------
// Practice Tools (Plugin Skills)
// ---------------------------------------------------------------------------

var PLUGIN_SKILLS = {
  'commercial-legal': ['vendor-agreement-review', 'nda-review', 'saas-msa-review'],
  'privacy-legal': ['dpa-review', 'dsar-response', 'pia-generation'],
  'litigation-legal': ['matter-intake', 'claim-chart', 'demand-draft'],
  'employment-legal': ['hire-review', 'termination-review', 'classification-analysis'],
  'product-legal': ['launch-review', 'marketing-claims-check'],
  'ip-legal': ['trademark-clearance', 'fto-analysis'],
  'ai-governance-legal': ['use-case-triage', 'impact-assessment'],
  'regulatory-legal': ['reg-gap-analysis', 'policy-diff'],
};

var skillFullOutput = '';

function initPracticeTools() {
  var pluginSel = document.getElementById('plugin-select');
  var skillSel  = document.getElementById('skill-select');
  var runBtn    = document.getElementById('btn-run-skill');

  pluginSel.addEventListener('change', function () {
    var plugin = pluginSel.value;
    skillSel.innerHTML = '<option value="">Select skill...</option>';
    skillSel.disabled = !plugin;
    runBtn.disabled = true;
    if (!plugin) return;
    var skills = PLUGIN_SKILLS[plugin] || [];
    skills.forEach(function (s) {
      var opt = document.createElement('option');
      opt.value = s;
      opt.textContent = s.replace(/-/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
      skillSel.appendChild(opt);
    });
  });

  skillSel.addEventListener('change', function () {
    runBtn.disabled = !skillSel.value;
  });

  document.getElementById('btn-review-selection').addEventListener('click', function () {
    Office.context.document.getSelectedDataAsync(
      Office.CoercionType.Text,
      function (result) {
        if (result.status === Office.AsyncResultStatus.Succeeded && result.value) {
          document.getElementById('skill-input').value = result.value;
        } else {
          setStatus('No text selected in Word document.');
        }
      }
    );
  });

  runBtn.addEventListener('click', runSkill);

  document.getElementById('btn-show-full').addEventListener('click', function () {
    document.getElementById('skill-result-full').classList.remove('hidden');
    this.classList.add('hidden');
  });

  document.getElementById('btn-insert-skill').addEventListener('click', function () {
    if (skillFullOutput) {
      Office.context.document.setSelectedDataAsync(
        skillFullOutput,
        { coercionType: Office.CoercionType.Text },
        function (result) {
          if (result.status === Office.AsyncResultStatus.Succeeded) {
            setStatus('Draft inserted at cursor.');
          }
        }
      );
    }
  });

  // Nav tabs
  document.querySelectorAll('.nav-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('.nav-tab').forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      var section = tab.getAttribute('data-section');
      document.getElementById('chat-section').classList.toggle('hidden', section !== 'chat');
      document.getElementById('tools-section').classList.toggle('hidden', section !== 'tools');
      document.getElementById('docs-section').classList.toggle('hidden', section !== 'docs');
    });
  });
}

function runSkill() {
  var plugin  = document.getElementById('plugin-select').value;
  var skill   = document.getElementById('skill-select').value;
  var input   = document.getElementById('skill-input').value.trim();
  var premium = document.getElementById('skill-premium').checked;

  if (!plugin || !skill || !input) {
    setStatus('Select plugin, skill, and provide input text.'); return;
  }
  if (!state.token) { setStatus('Please sign in first.'); return; }

  var runBtn = document.getElementById('btn-run-skill');
  runBtn.disabled = true;
  runBtn.textContent = 'Running...';
  setStatus('Executing skill...');

  fetch(API_BASE + '/plugins/' + plugin + '/' + skill, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + state.token },
    body: JSON.stringify({ skill: skill, input_text: input, use_premium: premium }),
  })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var outputArea = document.getElementById('skill-output-area');
      var gatesEl    = document.getElementById('skill-gates');
      var previewEl  = document.getElementById('skill-result-preview');
      var fullEl     = document.getElementById('skill-result-full');
      var showBtn    = document.getElementById('btn-show-full');

      outputArea.classList.remove('hidden');
      skillFullOutput = data.memo || '';

      if (data.gates_triggered && data.gates_triggered.length > 0) {
        gatesEl.innerHTML = data.gates_triggered.map(function (g) {
          return '<div class="gate-msg">🚫 ' + g + '</div>';
        }).join('');
        previewEl.textContent = '';
        fullEl.textContent = '';
      } else {
        gatesEl.innerHTML = '';
        var preview = skillFullOutput.substring(0, 500);
        var hasMore = skillFullOutput.length > 500;
        previewEl.textContent = preview + (hasMore ? '...' : '');
        fullEl.textContent = skillFullOutput;
        if (hasMore) {
          showBtn.classList.remove('hidden');
          fullEl.classList.add('hidden');
        } else {
          showBtn.classList.add('hidden');
          fullEl.classList.remove('hidden');
        }
      }
      setStatus('Skill complete. Attorney review required before use.');
    })
    .catch(function (err) {
      setStatus('Error: ' + err.message);
    })
    .finally(function () {
      runBtn.disabled = false;
      runBtn.textContent = 'Run Skill';
    });
}

// ---------------------------------------------------------------------------
// Office.onReady
// ---------------------------------------------------------------------------

Office.onReady(function (info) {
  if (info.host === Office.HostType.Word) {
    init();
    initPracticeTools();
  } else {
    init();
    initPracticeTools();
  }
});
