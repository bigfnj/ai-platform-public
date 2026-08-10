// BrokerTray — a lightweight system-tray status/control for the platform GPU/Model
// Broker. Runs in the interactive user session (a Windows service can't show a tray
// icon — Session 0 isolation). It only talks to the broker's HTTP API, so it controls
// Ollama AND the media models uniformly.
//
//   icon: GREEN  = a model is loaded (ready in VRAM)
//         YELLOW = Ollama up but no model loaded (cold)
//         RED    = Ollama/broker unreachable
//   right-click:
//     <loaded model(s)>   (top; hover a row -> "unload", click to unload just that model)
//     Endpoints (Broker + Ollama base URL + port; click a row to copy)
//     [Update Ollama (vX) available]   (only when an update exists -> opens download)
//     -------
//     Load model  >  (every installed model; checkmark on the loaded one[s])
//     Unload after >  5 / 15 / 30 / 60 minutes / Never  (checkmark on current)
//     -------
//     Service >  Stop / Restart / Remove NSSM Service (Permanent)   [elevated]
//     -------
//     Autorun (checkbox)  |  Open dashboard  |  Edit dashboard location…  |  Exit
//
// Update check runs ONCE at startup (per the user's "check on load"). Build:
// tools\broker-tray\build.ps1 (built-in .NET Framework csc, no SDK).
// Config: BROKER_URL env (default http://127.0.0.1:11500). BROKER_AUTH_TOKEN is read
// from the environment or, for the repo-local tray, deploy\.env.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using Microsoft.Win32;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class TimeoutWebClient : WebClient
{
    public int TimeoutMs = 5000;
    protected override WebRequest GetWebRequest(Uri address)
    {
        WebRequest r = base.GetWebRequest(address);
        if (r != null) r.Timeout = TimeoutMs;
        return r;
    }
}

class BrokerTray : ApplicationContext
{
    const string OllamaDownloadUrl = "https://ollama.com/download";
    const string OllamaLatestApi = "https://api.github.com/repos/ollama/ollama/releases/latest";

    readonly string broker = Env("BROKER_URL", "http://127.0.0.1:11500");
    readonly string brokerToken = LoadBrokerToken();
    // The model host behind the broker, shown in the tray's Endpoints section. Defaults to
    // Ollama's standard local port; override with OLLAMA_URL if the broker points elsewhere.
    readonly string ollamaUrl = Env("OLLAMA_URL", "http://127.0.0.1:11434");
    string dashboardUrl = LoadDashboardUrl(); // persisted (registry); editable from the menu, no rebuild
    readonly string serviceName = Env("BROKER_SERVICE", "platform-broker");
    readonly string ollamaService = Env("OLLAMA_SERVICE", "ollama");
    readonly string nssmPath = Env("NSSM_PATH", @"D:\.claude\projects\ai-platform\deploy\bin\nssm.exe");
    readonly NotifyIcon icon = new NotifyIcon();
    readonly Timer timer = new Timer();
    readonly Dictionary<string, Icon> icons = new Dictionary<string, Icon>(); // state -> icon: "green"|"yellow"|"red"
    readonly JavaScriptSerializer js = new JavaScriptSerializer();

    string keepAlive = "30m"; // "Unload after" default: auto-unload 30m after idle (was -1/never)
    static readonly string[][] KeepOpts = new string[][] {
        new[] { "5 minutes", "5m" }, new[] { "15 minutes", "15m" },
        new[] { "30 minutes", "30m" }, new[] { "60 minutes", "60m" }, new[] { "Never", "-1" },
    };

    List<string> available = new List<string>();
    List<string> loaded = new List<string>();
    string mediaActive = null;  // model name while a media (image/tts) worker runs, else null
    string mediaOp = "";
    bool brokerUp = false;   // /v1/status responded
    bool reachable = false;  // ollama_reachable
    string ollamaVersion = "";
    string warning = "";     // contextual attention text, "" when all good

    volatile bool updateChecked = false;
    volatile bool updateAvailable = false;
    volatile string latestTag = "";
    bool balloonShown = false;
    string lastWarning = ""; // last warning we toasted, so we don't re-pop every poll

    static string Env(string k, string d)
    {
        string v = Environment.GetEnvironmentVariable(k);
        return string.IsNullOrEmpty(v) ? d : v;
    }

    static string LoadBrokerToken()
    {
        string token = Env("BROKER_AUTH_TOKEN", "").Trim();
        if (token != "") return token;

        try
        {
            string envFile = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, @"..\..\deploy\.env"));
            if (!File.Exists(envFile)) return "";
            foreach (string raw in File.ReadAllLines(envFile))
            {
                string line = raw.Trim();
                if (line == "" || line.StartsWith("#")) continue;
                int equals = line.IndexOf('=');
                if (equals < 0 || !line.Substring(0, equals).Trim().Equals("BROKER_AUTH_TOKEN", StringComparison.OrdinalIgnoreCase)) continue;
                token = line.Substring(equals + 1).Trim();
                if (token.Length >= 2 && ((token[0] == '"' && token[token.Length - 1] == '"') ||
                                          (token[0] == '\'' && token[token.Length - 1] == '\'')))
                    token = token.Substring(1, token.Length - 2);
                return token;
            }
        }
        catch { }
        return "";
    }

    [STAThread]
    static void Main()
    {
        // Single-instance guard: at login the Startup shortcut and Windows'
        // "restart my apps after sign-in" can EACH launch us, which is how two tray
        // icons appear. The first instance owns the mutex; any later one exits.
        bool createdNew;
        using (System.Threading.Mutex mtx =
                   new System.Threading.Mutex(true, "BrokerTray_SingleInstance_v1", out createdNew))
        {
            if (!createdNew) return;
            try { ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12; } catch { }
            Application.EnableVisualStyles();
            Application.Run(new BrokerTray());
        }
    }

    public BrokerTray()
    {
        LoadIcons();
        icon.Icon = PickIcon("red");
        icon.Text = "GPU Broker";
        icon.Visible = true;
        icon.ContextMenuStrip = new ContextMenuStrip();
        icon.ContextMenuStrip.Opening += (s, e) => { e.Cancel = false; BuildMenu(); };
        icon.DoubleClick += (s, e) => OpenDashboard();
        icon.BalloonTipClicked += (s, e) => { if (updateAvailable && !OllamaUpdateStaged()) StageOllamaUpdate(latestTag); };
        timer.Interval = 15000; // auto-poll every 15s; no manual refresh needed
        timer.Tick += (s, e) => Poll();
        timer.Start();
        Poll();
    }

    // Placeholder icon: a flat filled status dot (green/yellow/red), drawn in code
    // so the exe needs no image resources. Swap MakeDot for a real icon later
    // (icons\*.png + ollama-icon.svg are kept in the source tree for that).
    void LoadIcons()
    {
        icons["green"]  = MakeDot(Color.FromArgb(0x2E, 0xCC, 0x71)); // model loaded / ready
        icons["yellow"] = MakeDot(Color.FromArgb(0xF1, 0xC4, 0x0F)); // up, no model loaded
        icons["red"]    = MakeDot(Color.FromArgb(0xE7, 0x4C, 0x3C)); // broker/Ollama unreachable
    }

    // One antialiased circle in the status color on a transparent field. The HICON
    // is kept for the process lifetime (only three are ever made), matching how the
    // old embedded icons were cached once at startup.
    static Icon MakeDot(Color fill)
    {
        Bitmap bmp = new Bitmap(32, 32);
        using (Graphics g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            Rectangle r = new Rectangle(3, 3, 25, 25);
            using (SolidBrush b = new SolidBrush(fill)) g.FillEllipse(b, r);
            using (Pen p = new Pen(Color.FromArgb(150, 0, 0, 0), 2f)) g.DrawEllipse(p, r);
        }
        Icon ic = Icon.FromHandle(bmp.GetHicon());
        bmp.Dispose();
        return ic;
    }

    Icon PickIcon(string state)
    {
        Icon ic;
        return icons.TryGetValue(state, out ic) ? ic : icons["red"];
    }

    void Poll()
    {
        brokerUp = false;
        bool statusReady = false;
        bool authFailed = false;
        reachable = false;
        loaded = new List<string>();
        available = new List<string>();
        mediaActive = null; mediaOp = "";

        try
        {
            brokerUp = GetJson(broker + "/healthz", 3000, null) != null;
        }
        catch { brokerUp = false; }

        if (brokerUp) try
        {
            Dictionary<string, object> st = GetBrokerJson("/v1/status", 3000);
            statusReady = st != null;
            reachable = st != null && st.ContainsKey("ollama_reachable") && Convert.ToBoolean(st["ollama_reachable"]);
            loaded = Names(st, "loaded");
            if (st != null && st.ContainsKey("media") && st["media"] is Dictionary<string, object>)
            {
                Dictionary<string, object> md = (Dictionary<string, object>)st["media"];
                if (md.ContainsKey("active") && md["active"] is Dictionary<string, object>)
                {
                    Dictionary<string, object> a = (Dictionary<string, object>)md["active"];
                    string mdl = a.ContainsKey("model") && a["model"] != null ? a["model"].ToString() : "";
                    mediaOp = a.ContainsKey("op") && a["op"] != null ? a["op"].ToString() : "";
                    mediaActive = mdl != "" ? mdl : (mediaOp != "" ? mediaOp : "media");
                }
            }
            if (st != null && st.ContainsKey("ollama_version") && st["ollama_version"] != null)
                ollamaVersion = st["ollama_version"].ToString();
        }
        catch (WebException ex) { authFailed = IsUnauthorized(ex); }
        catch { }

        if (statusReady) try { available = Names(GetBrokerJson("/v1/models", 3000), "models"); }
        catch (WebException ex) { authFailed = IsUnauthorized(ex); }
        catch { }

        // Contextual warning (most-severe first); "" when nothing needs attention.
        if (!brokerUp) warning = "⚠ Broker unreachable at " + broker;
        else if (authFailed) warning = "⚠ Broker authentication failed";
        else if (!statusReady) warning = "⚠ Broker status unavailable at " + broker;
        else if (!reachable) warning = "⚠ Ollama is down (the broker is up)";
        else if (available.Count == 0) warning = "⚠ No Ollama models installed";
        else warning = "";

        // Fading toast when a warning NEWLY appears (or changes); not on every poll.
        if (warning != "" && warning != lastWarning)
        {
            lastWarning = warning;
            icon.ShowBalloonTip(6000, "GPU Broker", warning, ToolTipIcon.Warning);
        }
        else if (warning == "") lastWarning = "";

        bool busy = loaded.Count > 0 || mediaActive != null;  // green when the GPU is working (Ollama OR media)
        bool up = brokerUp && statusReady && reachable;
        icon.Icon = PickIcon(!up ? "red" : (busy ? "green" : "yellow"));
        List<string> shown = new List<string>(loaded);
        if (mediaActive != null) shown.Add(mediaActive + " (" + (mediaOp == "" ? "media" : mediaOp) + ")");
        string tip;
        if (!brokerUp) tip = "GPU Broker: broker offline";
        else if (authFailed) tip = "GPU Broker: authentication failed";
        else if (!statusReady) tip = "GPU Broker: status unavailable";
        else if (!reachable) tip = "GPU Broker: Ollama down";
        else if (shown.Count > 0) tip = "GPU Broker: " + string.Join(", ", shown.ToArray());
        else tip = "GPU Broker: up, no model loaded";
        icon.Text = tip.Length <= 63 ? tip : tip.Substring(0, 62) + "…";

        // Update check: once, after we know the running version (i.e. "on load").
        if (up && !updateChecked && ollamaVersion != "")
        {
            updateChecked = true;
            System.Threading.ThreadPool.QueueUserWorkItem(delegate { CheckForUpdate(ollamaVersion); });
        }
        // Show the update balloon once, on the UI thread.
        if (updateAvailable && !balloonShown)
        {
            balloonShown = true;
            icon.ShowBalloonTip(8000, "Ollama update available",
                latestTag + " is available. Right-click the tray icon to download.", ToolTipIcon.Info);
        }
    }

    void CheckForUpdate(string running)
    {
        try
        {
            Dictionary<string, object> rel = GetJson(OllamaLatestApi, 8000, "BrokerTray");
            if (rel != null && rel.ContainsKey("tag_name") && rel["tag_name"] != null)
            {
                string tag = rel["tag_name"].ToString();
                if (IsNewer(tag, running)) { latestTag = tag; updateAvailable = true; }
            }
        }
        catch { }
    }

    static bool IsNewer(string latest, string running)
    {
        int[] a = Ver(latest), b = Ver(running);
        int n = Math.Max(a.Length, b.Length);
        for (int i = 0; i < n; i++)
        {
            int x = i < a.Length ? a[i] : 0, y = i < b.Length ? b[i] : 0;
            if (x != y) return x > y;
        }
        return false;
    }

    static int[] Ver(string s)
    {
        if (s == null) return new int[0];
        s = s.TrimStart('v', 'V');
        string[] parts = s.Split('.', '-', '+');
        List<int> r = new List<int>();
        foreach (string p in parts)
        {
            string digits = "";
            foreach (char c in p) { if (c >= '0' && c <= '9') digits += c; else break; }
            if (digits.Length == 0) break;
            r.Add(int.Parse(digits));
        }
        return r.ToArray();
    }

    Dictionary<string, object> GetJson(string url, int timeoutMs, string userAgent)
    {
        using (TimeoutWebClient wc = new TimeoutWebClient { TimeoutMs = timeoutMs })
        {
            wc.Encoding = Encoding.UTF8;
            if (userAgent != null) wc.Headers[HttpRequestHeader.UserAgent] = userAgent;
            return js.DeserializeObject(wc.DownloadString(url)) as Dictionary<string, object>;
        }
    }

    Dictionary<string, object> GetBrokerJson(string path, int timeoutMs)
    {
        using (TimeoutWebClient wc = new TimeoutWebClient { TimeoutMs = timeoutMs })
        {
            wc.Encoding = Encoding.UTF8;
            AddBrokerAuth(wc);
            return js.DeserializeObject(wc.DownloadString(broker + path)) as Dictionary<string, object>;
        }
    }

    void AddBrokerAuth(WebClient wc)
    {
        if (brokerToken != "") wc.Headers[HttpRequestHeader.Authorization] = "Bearer " + brokerToken;
    }

    static bool IsUnauthorized(WebException ex)
    {
        HttpWebResponse response = ex.Response as HttpWebResponse;
        return response != null && response.StatusCode == HttpStatusCode.Unauthorized;
    }

    List<string> Names(Dictionary<string, object> d, string key)
    {
        List<string> r = new List<string>();
        if (d != null && d.ContainsKey(key) && d[key] is object[])
        {
            foreach (object o in (object[])d[key])
            {
                Dictionary<string, object> m = o as Dictionary<string, object>;
                if (m != null && m.ContainsKey("name") && m["name"] != null) r.Add(m["name"].ToString());
            }
        }
        return r;
    }

    void Post(string path, string body)
    {
        System.Threading.ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                using (TimeoutWebClient wc = new TimeoutWebClient { TimeoutMs = 600000 })
                {
                    wc.Headers[HttpRequestHeader.ContentType] = "application/json";
                    AddBrokerAuth(wc);
                    wc.UploadString(broker + path, "POST", body);
                }
            }
            catch { }
        });
    }

    static string J(string s) { return s.Replace("\\", "\\\\").Replace("\"", "\\\""); }

    // A small read-only "Endpoints" section: the base URL + port the tray talks to (the
    // broker) and the model host behind it (Ollama). Each row is click-to-copy so the URL
    // is one click away when wiring up a rail, a client, or a debug curl.
    void AddEndpoints(ContextMenuStrip m)
    {
        ToolStripMenuItem hdr = new ToolStripMenuItem("Endpoints (click to copy)");
        hdr.Enabled = false;
        m.Items.Add(hdr);
        AddEndpoint(m, "Broker", broker);
        AddEndpoint(m, "Ollama", ollamaUrl);
        m.Items.Add(new ToolStripSeparator());
    }

    void AddEndpoint(ContextMenuStrip m, string label, string url)
    {
        ToolStripMenuItem it = new ToolStripMenuItem("    " + label + ":  " + url);
        it.ToolTipText = "Click to copy";
        string captured = url;
        it.Click += delegate { try { Clipboard.SetText(captured); } catch { } };
        m.Items.Add(it);
    }

    void BuildMenu()
    {
        ContextMenuStrip m = icon.ContextMenuStrip;
        m.Items.Clear();

        // Loaded model(s) at the very top — at-a-glance readout. Hover an Ollama row to reveal
        // "unload"; click to unload just that model. A running media (image/tts) job is shown too,
        // but greyed — it's a transient worker that exits on its own (nothing to unload).
        if (loaded.Count > 0 || mediaActive != null)
        {
            foreach (string name in loaded)
            {
                ToolStripMenuItem mi = new ToolStripMenuItem(name);
                mi.ToolTipText = "Click to unload";
                string captured = name;
                mi.MouseEnter += delegate { mi.ShortcutKeyDisplayString = "unload"; };
                mi.MouseLeave += delegate { mi.ShortcutKeyDisplayString = ""; };
                mi.Click += delegate { Post("/v1/unload", "{\"model\":\"" + J(captured) + "\"}"); Soon(); };
                m.Items.Add(mi);
            }
            if (mediaActive != null)
            {
                ToolStripMenuItem mm = new ToolStripMenuItem(mediaActive + " · " + (mediaOp == "" ? "media" : mediaOp) + " (rendering…)");
                mm.Enabled = false;
                m.Items.Add(mm);
            }
            m.Items.Add(new ToolStripSeparator());
        }
        else if (reachable)
        {
            ToolStripMenuItem none = new ToolStripMenuItem("No model loaded");
            none.Enabled = false;
            m.Items.Add(none);
            m.Items.Add(new ToolStripSeparator());
        }

        // Endpoints the tray is talking to (base URL + port), click a row to copy it.
        AddEndpoints(m);

        if (warning != "")
        {
            ToolStripLabel warn = new ToolStripLabel(warning);
            warn.ForeColor = Color.Firebrick;
            warn.Font = new Font(m.Font, FontStyle.Bold);
            m.Items.Add(warn);
            m.Items.Add(new ToolStripSeparator());
        }

        if (OllamaUpdateStaged())
        {
            ToolStripMenuItem staged = new ToolStripMenuItem("✓ Ollama update staged — installs on next restart");
            staged.Enabled = false;
            m.Items.Add(staged);
            ToolStripMenuItem cancelUpd = new ToolStripMenuItem("Cancel staged Ollama update");
            cancelUpd.Click += delegate { CancelStagedUpdate(); Soon(); };
            m.Items.Add(cancelUpd);
            m.Items.Add(new ToolStripSeparator());
        }
        else if (updateAvailable)
        {
            ToolStripMenuItem up = new ToolStripMenuItem("⬆ Update Ollama to " + latestTag + " (on next restart)");
            up.Font = new Font(m.Font, FontStyle.Bold);
            string tag = latestTag;
            up.Click += delegate { StageOllamaUpdate(tag); };
            m.Items.Add(up);
            m.Items.Add(new ToolStripSeparator());
        }

        ToolStripMenuItem load = new ToolStripMenuItem("Load model");
        load.Enabled = reachable;
        if (available.Count == 0)
        {
            ToolStripMenuItem none = new ToolStripMenuItem(reachable ? "(no models installed)" : "(broker offline)");
            none.Enabled = false;
            load.DropDownItems.Add(none);
        }
        foreach (string name in available)
        {
            ToolStripMenuItem it = new ToolStripMenuItem(name);
            it.Checked = loaded.Contains(name);
            string captured = name;
            it.Click += delegate { Post("/v1/load", "{\"model\":\"" + J(captured) + "\",\"keep_alive\":\"" + keepAlive + "\"}"); Soon(); };
            load.DropDownItems.Add(it);
        }
        m.Items.Add(load);

        ToolStripMenuItem after = new ToolStripMenuItem("Unload after");
        foreach (string[] opt in KeepOpts)
        {
            ToolStripMenuItem it = new ToolStripMenuItem(opt[0]);
            it.Checked = keepAlive == opt[1];
            string val = opt[1];
            it.Click += delegate
            {
                keepAlive = val;
                foreach (string n in loaded) Post("/v1/load", "{\"model\":\"" + J(n) + "\",\"keep_alive\":\"" + val + "\"}");
                Soon();
            };
            after.DropDownItems.Add(it);
        }
        m.Items.Add(after);
        m.Items.Add(new ToolStripSeparator());

        ToolStripMenuItem svc = new ToolStripMenuItem("Service");
        ToolStripMenuItem svcStop = new ToolStripMenuItem("Stop Service");
        svcStop.Click += delegate { RunNssm("stop " + serviceName); Soon(); };
        ToolStripMenuItem svcRestart = new ToolStripMenuItem("Restart Service");
        svcRestart.Click += delegate { RunNssm("restart " + serviceName); Soon(); };
        ToolStripMenuItem svcRemove = new ToolStripMenuItem("Remove NSSM Service (Permanent)");
        svcRemove.Click += delegate { RemoveService(); };
        svc.DropDownItems.Add(svcStop);
        svc.DropDownItems.Add(svcRestart);
        svc.DropDownItems.Add(new ToolStripSeparator());
        svc.DropDownItems.Add(svcRemove);
        m.Items.Add(svc);
        m.Items.Add(new ToolStripSeparator());

        ToolStripMenuItem autorun = new ToolStripMenuItem("Autorun (start at login)");
        autorun.Checked = IsAutorun();
        autorun.Click += delegate { ToggleAutorun(); };
        m.Items.Add(autorun);

        ToolStripMenuItem open = new ToolStripMenuItem("Open dashboard");
        open.Click += delegate { OpenDashboard(); };
        m.Items.Add(open);
        ToolStripMenuItem editLoc = new ToolStripMenuItem("Edit dashboard location…");
        editLoc.Click += delegate { string u = PromptForUrl(dashboardUrl); if (!string.IsNullOrEmpty(u)) { dashboardUrl = u; SaveDashboardUrl(u); } };
        m.Items.Add(editLoc);
        m.Items.Add(new ToolStripSeparator());
        ToolStripMenuItem exit = new ToolStripMenuItem("Exit");
        exit.Click += delegate { icon.Visible = false; timer.Stop(); Application.Exit(); };
        m.Items.Add(exit);
    }

    // --- autorun (Startup-folder shortcut) ----------------------------------
    static string LnkPath()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "BrokerTray.lnk");
    }
    static bool IsAutorun() { return File.Exists(LnkPath()); }
    void ToggleAutorun()
    {
        try
        {
            string lnk = LnkPath();
            if (File.Exists(lnk)) { File.Delete(lnk); return; }
            Type t = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(t);
            dynamic sc = shell.CreateShortcut(lnk);
            sc.TargetPath = Application.ExecutablePath;
            sc.WorkingDirectory = Path.GetDirectoryName(Application.ExecutablePath);
            sc.Description = "GPU Broker tray";
            sc.Save();
        }
        catch (Exception ex)
        {
            MessageBox.Show("Could not change autorun: " + ex.Message, "BrokerTray");
        }
    }

    void OpenDashboard() { Open(dashboardUrl); }
    static void Open(string url) { try { Process.Start(url); } catch { } }

    // --- dashboard location (persisted so a URL change never needs a rebuild) ----
    // Default assumes caddy on host :1111 (host 1111 -> container :80, Host platform.localhost).
    const string DefaultDashboard = "http://platform.localhost:1111";
    const string RegSubKey = @"Software\BrokerTray";
    static string LoadDashboardUrl()
    {
        try
        {
            using (RegistryKey k = Registry.CurrentUser.OpenSubKey(RegSubKey))
            {
                object v = k == null ? null : k.GetValue("DashboardUrl");
                if (v != null && !string.IsNullOrEmpty(v.ToString())) return v.ToString();
            }
        }
        catch { }
        return Env("DASHBOARD_URL", DefaultDashboard); // env fallback, then the baked default
    }
    static void SaveDashboardUrl(string url)
    {
        try { using (RegistryKey k = Registry.CurrentUser.CreateSubKey(RegSubKey)) { if (k != null) k.SetValue("DashboardUrl", url); } }
        catch (Exception ex) { MessageBox.Show("Could not save dashboard location: " + ex.Message, "BrokerTray"); }
    }
    // Minimal prompt (no Microsoft.VisualBasic reference needed).
    static string PromptForUrl(string current)
    {
        using (Form f = new Form {
            Text = "Dashboard location", Width = 470, Height = 160, ShowInTaskbar = false,
            FormBorderStyle = FormBorderStyle.FixedDialog, StartPosition = FormStartPosition.CenterScreen,
            MinimizeBox = false, MaximizeBox = false })
        {
            Label lbl = new Label { Left = 14, Top = 14, Width = 430, Text = "URL the tray opens for “Open dashboard”:" };
            TextBox tb = new TextBox { Left = 14, Top = 40, Width = 430, Text = current };
            Button ok = new Button { Text = "Save", Left = 288, Top = 76, Width = 75, DialogResult = DialogResult.OK };
            Button cancel = new Button { Text = "Cancel", Left = 369, Top = 76, Width = 75, DialogResult = DialogResult.Cancel };
            f.Controls.Add(lbl); f.Controls.Add(tb); f.Controls.Add(ok); f.Controls.Add(cancel);
            f.AcceptButton = ok; f.CancelButton = cancel;
            return f.ShowDialog() == DialogResult.OK ? tb.Text.Trim() : null;
        }
    }

    // --- NSSM service control (elevated; runas is silent for the passwordless admin here) ---
    void RunNssm(string args) { RunElevated(nssmPath, args); }
    static void RunElevated(string file, string args)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = file,
                Arguments = args,
                UseShellExecute = true,     // required for the runas verb
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
            });
        }
        catch (Exception ex) { MessageBox.Show("Could not run service command: " + ex.Message, "BrokerTray"); }
    }
    void RemoveService()
    {
        DialogResult ok = MessageBox.Show(
            "Permanently remove the \"" + serviceName + "\" NSSM service?\n\n" +
            "The broker will stop and will NOT start at boot until it is reinstalled " +
            "(deploy\\install-services.ps1). This does not delete any files.",
            "Remove service", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
        if (ok != DialogResult.Yes) return;
        // stop then remove in a single elevation (nssm path has no spaces, so no quoting needed)
        RunElevated("cmd.exe", "/c " + nssmPath + " stop " + serviceName + " & " + nssmPath + " remove " + serviceName + " confirm");
        Soon();
    }

    // --- seamless Ollama update (Mode A: stage now, silent-install on next restart) ------
    // The tray already detects a newer release; this makes applying it frictionless. "Update"
    // launches update-ollama.ps1 (elevated) which downloads the exact OllamaSetup.exe and
    // registers a one-shot SYSTEM "at startup" task; on the next boot that task stops the
    // ollama service, silent-installs, and restarts it. No manual MSI / closing apps / relaunch.
    string UpdaterScript()
    {
        return Path.Combine(Path.GetDirectoryName(Application.ExecutablePath) ?? ".", "update-ollama.ps1");
    }
    static string StagedMarker()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                            "BrokerTray", "ollama-update", "STAGED");
    }
    static bool OllamaUpdateStaged() { return File.Exists(StagedMarker()); }

    void StageOllamaUpdate(string tag)
    {
        string script = UpdaterScript();
        if (!File.Exists(script))
        {
            MessageBox.Show("Updater not found next to BrokerTray.exe:\n" + script, "BrokerTray");
            return;
        }
        icon.ShowBalloonTip(9000, "Ollama update staging",
            "Downloading Ollama " + tag + " in the background (~1.5 GB). It installs automatically "
            + "on your next restart — no clicking through an installer.", ToolTipIcon.Info);
        RunElevated("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Mode Stage -Tag " + tag
            + " -Service " + ollamaService);
    }

    void CancelStagedUpdate()
    {
        RunElevated("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -Command \"Unregister-ScheduledTask -TaskName "
            + "BrokerTray-OllamaUpdate -Confirm:$false -ErrorAction SilentlyContinue; Remove-Item "
            + "-LiteralPath '" + StagedMarker() + "' -Force -ErrorAction SilentlyContinue\"");
    }

    void Soon()
    {
        Timer t = new Timer { Interval = 1300 };
        t.Tick += delegate { t.Stop(); t.Dispose(); Poll(); };
        t.Start();
    }
}
