/* Cleanup script generator (bash for macOS/Linux, PowerShell for Windows).
 *
 * Safety properties of every generated script:
 *  - dry-run unless --apply / -Apply is passed, and --apply asks for confirmation
 *  - moves to Trash / Recycle Bin; never rm -rf / Remove-Item -Recurse -Force
 *  - re-checks at run time: path exists, is not a symlink/junction, is not under a
 *    protected (NEVER) path, the owning app is not running
 *  - official tool commands are preferred (npm cache clean, conda clean, …)
 */
(function (root) {
  "use strict";

  function qsh(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }
  function qps(s) { return "'" + String(s).replace(/['‘’‚‛]/g, function (m) { return m + m; }) + "'"; }

  function fmtBytes(n) {
    n = Number(n) || 0;
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i < 2 ? Math.round(n) : n.toFixed(n >= 100 ? 0 : 1)) + " " + u[i];
  }

  var CAT = { safe: "SAFE", review: "REVIEW" };

  function actionable(it) {
    return it && it.selectable && it.cat !== "never" && it.kind !== "advice" && it.kind !== "report";
  }

  function stamp() {
    var d = new Date();
    function p(x) { return (x < 10 ? "0" : "") + x; }
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes());
  }

  function sortItems(items) {
    return items.slice().sort(function (a, b) {
      if (a.cat !== b.cat) return a.cat === "safe" ? -1 : 1;
      return (b.size || 0) - (a.size || 0);
    });
  }

  function totals(items) {
    var full = 0, partial = 0, unknown = 0;
    items.forEach(function (it) {
      if (it.reclaim === "full") full += it.size || 0;
      else if (it.reclaim === "partial") partial += it.size || 0;
      else unknown += 1;
    });
    return { full: full, partial: partial, unknown: unknown };
  }

  function oneLine(s) { return String(s || "").replace(/[\r\n]+/g, " ").slice(0, 300); }

  // ------------------------------------------------------------------ bash
  function genBash(data, items) {
    var env = data.env, isMac = env.os === "macos";
    var t = totals(items);
    var L = [];
    L.push("#!/usr/bin/env bash");
    L.push("# =====================================================================");
    L.push("#  disk-cleaner temizlik script'i (" + (isMac ? "macOS" : "Linux") + ")");
    L.push("#  Rapor: " + oneLine(env.host) + " / " + data.meta.generated + "   Script: " + stamp());
    L.push("#  Seçili öğe: " + items.length + "   Tahmini kazanç: " + fmtBytes(t.full) +
      (t.partial ? " + en fazla " + fmtBytes(t.partial) + " (kısmi)" : ""));
    L.push("#");
    L.push("#  VARSAYILAN: DRY-RUN. Hiçbir şey değişmez; sadece yapılacakları listeler.");
    L.push("#  Gerçekten uygulamak için:   bash " + "<bu-dosya> --apply");
    L.push("#  Dosyalar silinmez, Çöp Kutusu'na taşınır (gio trash / trash-put / macOS trash).");
    L.push("#  Çöp kutusu aracı yoksa o öğe ATLANIR; script asla rm -rf kullanmaz.");
    L.push("#  Yer, Çöp Kutusu boşaltıldığında açılır.");
    L.push("# =====================================================================");
    L.push("set -u");
    L.push("APPLY=0; YES=0");
    L.push("for arg in \"$@\"; do");
    L.push("  case \"$arg\" in");
    L.push("    --apply) APPLY=1 ;;");
    L.push("    --dry-run) APPLY=0 ;;");
    L.push("    --yes) YES=1 ;;");
    L.push("    *) echo \"Kullanım: bash $0 [--dry-run | --apply [--yes]]\"; exit 2 ;;");
    L.push("  esac");
    L.push("done");
    L.push("if [ \"$(uname -s)\" != " + qsh(isMac ? "Darwin" : "Linux") + " ]; then");
    L.push("  echo 'Bu script " + (isMac ? "macOS" : "Linux") + " için üretildi.'; exit 2");
    L.push("fi");
    L.push("if [ \"$(id -u)\" = 0 ] && [ \"${DISK_CLEANER_ALLOW_ROOT:-}\" != 1 ]; then echo 'root olarak çalıştırma; normal kullanıcınla çalıştır.'; exit 2; fi");
    L.push("if [ \"$HOME\" != " + qsh(env.home) + " ]; then");
    L.push("  echo \"UYARI: Bu script \"" + qsh(env.home) + "\" ev klasörü için üretildi (şu an: $HOME).\"");
    L.push("  echo 'Farklı bir kullanıcı/bilgisayarda çalıştırma. Çıkılıyor.'; exit 2");
    L.push("fi");
    L.push("");
    L.push("GUARD_HARD=(");
    data.guard.hard.forEach(function (g) { L.push("  " + qsh(g)); });
    L.push(")");
    L.push("GUARD_EXACT=(");
    data.guard.exact.forEach(function (g) { L.push("  " + qsh(g)); });
    L.push(")");
    L.push("lc() { " + (isMac ? "printf '%s' \"$1\" | tr '[:upper:]' '[:lower:]';" : "printf '%s' \"$1\";") + " }");
    L.push("strip() { local s=\"$1\"; while [ \"${#s}\" -gt 1 ] && [ \"${s%/}\" != \"$s\" ]; do s=\"${s%/}\"; done; printf '%s' \"$s\"; }");
    L.push("G_HARD=(); for g in \"${GUARD_HARD[@]}\"; do G_HARD+=(\"$(lc \"$(strip \"$g\")\")\"); done");
    L.push("G_EXACT=(); for g in \"${GUARD_EXACT[@]}\"; do G_EXACT+=(\"$(lc \"$(strip \"$g\")\")\"); done");
    L.push("guarded() {");
    L.push("  local p g; p=\"$(lc \"$(strip \"$1\")\")\"");
    L.push("  case \"$p\" in ''|/) return 0 ;; esac");
    L.push("  case \"$p/\" in */.git/*) return 0 ;; esac");
    L.push("  for g in \"${G_EXACT[@]}\"; do [ \"$p\" = \"$g\" ] && return 0; done");
    L.push("  for g in \"${G_HARD[@]}\"; do case \"$p/\" in \"$g\"/*) return 0 ;; esac; done");
    L.push("  return 1");
    L.push("}");
    L.push("");
    L.push("TRASH_TOOL=''");
    if (isMac) {
      L.push("if [ -x /usr/bin/trash ]; then TRASH_TOOL=mac-trash; elif command -v trash >/dev/null 2>&1; then TRASH_TOOL=trash; else TRASH_TOOL=finder; fi");
    } else {
      L.push("if command -v gio >/dev/null 2>&1; then TRASH_TOOL=gio; elif command -v trash-put >/dev/null 2>&1; then TRASH_TOOL=trash-put; elif command -v kioclient5 >/dev/null 2>&1; then TRASH_TOOL=kioclient5; fi");
    }
    L.push("to_trash() {");
    L.push("  case \"$TRASH_TOOL\" in");
    L.push("    gio) gio trash -- \"$1\" ;;");
    L.push("    trash-put) trash-put -- \"$1\" ;;");
    L.push("    kioclient5) kioclient5 move \"$1\" trash:/ ;;");
    L.push("    mac-trash) /usr/bin/trash \"$1\" ;;");
    L.push("    trash) trash \"$1\" ;;");
    L.push("    finder) osascript -e 'on run argv' -e 'tell application \"Finder\" to delete (POSIX file (item 1 of argv) as alias)' -e 'end run' \"$1\" >/dev/null ;;");
    L.push("    *) echo \"    ! Çöp kutusu aracı yok (gio/trash-put). rm kullanılmaz; atlandı: $1\"; return 1 ;;");
    L.push("  esac");
    L.push("}");
    L.push("DONE=0; SKIPPED=0; FAILED=0; PLANNED=0; BYTES=0");
    L.push("say() { printf '%s\\n' \"$*\"; }");
    L.push("running() { local n; for n in \"$@\"; do if pgrep -i \"$n\" >/dev/null 2>&1; then printf '%s' \"$n\"; return 0; fi; done; return 1; }");
    L.push("# begin_item KATEGORİ BAŞLIK BOYUT_METNİ BAYT YOL [süreç...]");
    L.push("begin_item() {");
    L.push("  local cat=\"$1\" title=\"$2\" human=\"$3\" bytes=\"$4\" p=\"$5\"; shift 5");
    L.push("  say \"\"; say \"[$cat] $title  ($human)\"; say \"    $p\"");
    L.push("  if [ -L \"$p\" ]; then say '    - sembolik bağlantı, atlandı'; SKIPPED=$((SKIPPED+1)); return 1; fi");
    L.push("  if [ ! -e \"$p\" ]; then say '    - artık yok, atlandı'; SKIPPED=$((SKIPPED+1)); return 1; fi");
    L.push("  if guarded \"$p\"; then say '    ! KORUMALI (NEVER) yol, atlandı'; SKIPPED=$((SKIPPED+1)); return 1; fi");
    L.push("  local r; if [ \"$#\" -gt 0 ] && r=\"$(running \"$@\")\"; then say \"    - '$r' çalışıyor; kapatıp tekrar dene. Atlandı\"; SKIPPED=$((SKIPPED+1)); return 1; fi");
    L.push("  PLANNED=$((PLANNED+1)); BYTES=$((BYTES+bytes)); return 0");
    L.push("}");
    L.push("trash_one() {");
    L.push("  local c=\"$1\"");
    L.push("  if [ -L \"$c\" ] && [ ! -e \"$c\" ]; then :; fi");
    L.push("  if guarded \"$c\"; then say \"    ! korumalı, atlandı: $c\"; return 1; fi");
    L.push("  if [ \"$APPLY\" = 1 ]; then to_trash \"$c\" 2>/dev/null || { FAILED=$((FAILED+1)); return 1; }; fi");
    L.push("  return 0");
    L.push("}");
    L.push("act_trash() {");
    L.push("  if [ \"$APPLY\" = 1 ]; then");
    L.push("    if trash_one \"$1\"; then say '    -> Çöp Kutusuna taşındı'; DONE=$((DONE+1)); else say '    ! taşınamadı'; fi");
    L.push("  else say '    -> Çöp Kutusuna taşınacak'; fi");
    L.push("}");
    L.push("act_children() {  # act_children YOL [GÜN]");
    L.push("  local p=\"$1\" days=\"${2:-}\" n=0 k=0 c");
    L.push("  while IFS= read -r -d '' c; do");
    L.push("    n=$((n+1))");
    L.push("    if [ -n \"$days\" ] && [ -n \"$(find \"$c\" -xdev -mtime -\"$days\" -print -quit 2>/dev/null)\" ]; then continue; fi");
    L.push("    if [ -n \"$days\" ] && [ ! -O \"$c\" ]; then continue; fi");
    L.push("    if trash_one \"$c\"; then k=$((k+1)); fi");
    L.push("  done < <(find \"$p\" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)");
    L.push("  if [ \"$APPLY\" = 1 ]; then say \"    -> $k / $n öğe Çöp Kutusuna taşındı (kilitli/yeni olanlar atlandı)\"; DONE=$((DONE+1));");
    L.push("  else say \"    -> içindeki $k / $n öğe taşınacak\" ${days:+\"(son $days günde değişmeyenler)\"}; fi");
    L.push("}");
    L.push("act_command() {  # act_command ARAÇ KOMUT YEDEK YOL");
    L.push("  local tool=\"$1\" cmd=\"$2\" fb=\"$3\" p=\"$4\" ok=0");
    L.push("  case \"$tool\" in /*) [ -x \"$tool\" ] && ok=1 ;; *) command -v \"$tool\" >/dev/null 2>&1 && ok=1 ;; esac");
    L.push("  if [ \"$ok\" = 1 ]; then");
    L.push("    say \"    -> resmi komut: $cmd\"");
    L.push("    if [ \"$APPLY\" = 1 ]; then if eval \"$cmd\"; then DONE=$((DONE+1)); else say '    ! komut hata verdi'; FAILED=$((FAILED+1)); fi; fi");
    L.push("  elif [ \"$fb\" = trash ]; then say \"    - '$tool' bulunamadı; yedek: klasör Çöp Kutusuna\"; act_trash \"$p\"");
    L.push("  elif [ \"$fb\" = trash_children ]; then say \"    - '$tool' bulunamadı; yedek: içerik Çöp Kutusuna\"; act_children \"$p\"");
    L.push("  else say \"    - '$tool' bulunamadı; atlandı\"; SKIPPED=$((SKIPPED+1)); fi");
    L.push("}");
    L.push("");
    L.push("say '=== disk-cleaner' \"$( [ \"$APPLY\" = 1 ] && echo UYGULA || echo DRY-RUN )\" '==='");
    L.push("[ \"$APPLY\" = 1 ] || say 'DRY-RUN: hiçbir dosya değişmeyecek.'");
    L.push("if [ \"$APPLY\" = 1 ] && [ \"$YES\" != 1 ]; then");
    L.push("  printf '%s' " + qsh("Seçili " + items.length + " öğe Çöp Kutusuna taşınacak / resmi komutları çalıştırılacak. Devam için EVET yaz: "));
    L.push("  read -r ans; [ \"$ans\" = EVET ] || { say 'Vazgeçildi.'; exit 1; }");
    L.push("fi");
    items.forEach(function (it) {
      L.push("");
      L.push("# ---- #" + it.id + " " + oneLine(it.title));
      var args = [qsh(CAT[it.cat]), qsh(oneLine(it.title)), qsh(fmtBytes(it.size) + (it.reclaim === "partial" ? ", kısmi" : it.reclaim === "unknown" ? ", ölçülemedi" : "")),
        String(it.reclaim === "full" ? (it.size || 0) : 0), qsh(it.path)].concat((it.procs || []).map(qsh));
      var body;
      if (it.kind === "trash") body = "act_trash " + qsh(it.path);
      else if (it.kind === "trash_children") body = "act_children " + qsh(it.path);
      else if (it.kind === "trash_old") body = "act_children " + qsh(it.path) + " " + (it.older || 7);
      else if (it.kind === "command") body = "act_command " + qsh(it.tool || "") + " " + qsh(it.cmd || "") + " " + qsh(it.fallback || "") + " " + qsh(it.path);
      else return;
      if (it.path === "docker://") {
        L.push("say ''; say " + qsh("[" + CAT[it.cat] + "] " + oneLine(it.title) + "  (" + fmtBytes(it.size) + ", kısmi)") + "; PLANNED=$((PLANNED+1))");
        L.push("act_command " + qsh(it.tool || "docker") + " " + qsh(it.cmd || "") + " '' ''");
        return;
      }
      L.push("if begin_item " + args.join(" ") + "; then " + body + "; fi");
    });
    L.push("");
    L.push("say ''");
    L.push("say '======================================================='");
    L.push("if [ \"$APPLY\" = 1 ]; then");
    L.push("  say \"Tamamlanan: $DONE  Atlanan: $SKIPPED  Hata: $FAILED\"");
    L.push("  say 'Yer, Çöp Kutusu boşaltıldığında açılır. Önce içeriğine göz at.'");
    L.push("else");
    L.push("  say \"DRY-RUN: $PLANNED öğe işlenecekti (tam ölçülen ~$((BYTES/1048576)) MB). Atlanacak: $SKIPPED\"");
    L.push("  say \"Uygulamak için: bash $0 --apply\"");
    L.push("fi");
    return L.join("\n") + "\n";
  }

  // ------------------------------------------------------------ PowerShell
  function genPs(data, items) {
    var env = data.env;
    var t = totals(items);
    var L = [];
    L.push("#Requires -Version 5.1");
    L.push("<#");
    L.push("  disk-cleaner temizlik script'i (Windows)");
    L.push("  Rapor: " + oneLine(env.host) + " / " + data.meta.generated + "   Script: " + stamp());
    L.push("  Seçili öğe: " + items.length + "   Tahmini kazanç: " + fmtBytes(t.full) +
      (t.partial ? " + en fazla " + fmtBytes(t.partial) + " (kısmi)" : ""));
    L.push("");
    L.push("  VARSAYILAN: DRY-RUN. Hiçbir şey değişmez; sadece yapılacakları listeler.");
    L.push("  Önizleme :  powershell -ExecutionPolicy Bypass -File .\\<bu-dosya>.ps1");
    L.push("  Uygula   :  powershell -ExecutionPolicy Bypass -File .\\<bu-dosya>.ps1 -Apply");
    L.push("  Dosyalar silinmez, Geri Dönüşüm Kutusu'na taşınır. Remove-Item -Recurse -Force");
    L.push("  kullanılmaz. Çok büyük öğeler için Windows 'kalıcı silinsin mi?' diye SORAR.");
    L.push("  Yönetici gerektiren öğeler, script yönetici olarak çalışmıyorsa atlanır.");
    L.push("#>");
    L.push("[CmdletBinding()]");
    L.push("param([switch]$Apply, [switch]$Yes)");
    L.push("Set-StrictMode -Version 2.0");
    L.push("$ErrorActionPreference = 'Stop'");
    L.push("if ($env:OS -ne 'Windows_NT') { Write-Host 'Bu script Windows için üretildi.'; exit 2 }");
    L.push("if ($env:USERPROFILE.TrimEnd('\\') -ne " + qps(env.home.replace(/\\+$/, "")) + ") {");
    L.push("  Write-Host ('UYARI: Bu script ' + " + qps(env.home) + " + ' kullanıcısı için üretildi (şu an: ' + $env:USERPROFILE + '). Çıkılıyor.'); exit 2");
    L.push("}");
    L.push("$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)");
    L.push("$GuardHard = @(");
    data.guard.hard.forEach(function (g) { L.push("  " + qps(g)); });
    L.push(")");
    L.push("$GuardExact = @(");
    data.guard.exact.forEach(function (g) { L.push("  " + qps(g)); });
    L.push(")");
    L.push("function DcNorm([string]$p) { $p = $p.Trim(); while ($p.Length -gt 3 -and $p.EndsWith('\\')) { $p = $p.Substring(0, $p.Length - 1) }; return $p.ToLowerInvariant() }");
    L.push("$GHard = @($GuardHard | ForEach-Object { DcNorm $_ })");
    L.push("$GExact = @($GuardExact | ForEach-Object { DcNorm $_ })");
    L.push("function Test-DcGuarded([string]$p) {");
    L.push("  $n = DcNorm $p");
    L.push("  if ($n.Length -le 3) { return $true }");
    L.push("  if ($n -match '\\\\\\.git(\\\\|$)') { return $true }");
    L.push("  foreach ($g in $GExact) { if ($n -eq $g) { return $true } }");
    L.push("  foreach ($g in $GHard) { if ($n -eq $g -or $n.StartsWith($g.TrimEnd('\\') + '\\')) { return $true } }");
    L.push("  return $false");
    L.push("}");
    L.push("if (-not ('DcRecycle' -as [type])) {");
    L.push("Add-Type -TypeDefinition @'");
    L.push("using System;");
    L.push("using System.Runtime.InteropServices;");
    L.push("public static class DcRecycle {");
    L.push("  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]");
    L.push("  struct SHFILEOPSTRUCT { public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags; public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle; }");
    L.push("  [DllImport(\"shell32.dll\", CharSet = CharSet.Unicode)] static extern int SHFileOperation(ref SHFILEOPSTRUCT op);");
    L.push("  // FO_DELETE + FOF_ALLOWUNDO (recycle) | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT | FOF_WANTNUKEWARNING");
    L.push("  public static int Send(string path) {");
    L.push("    SHFILEOPSTRUCT op = new SHFILEOPSTRUCT();");
    L.push("    op.wFunc = 3; op.pFrom = path + \"\\0\\0\"; op.fFlags = (ushort)(0x40 | 0x10 | 0x400 | 0x4 | 0x4000);");
    L.push("    int rc = SHFileOperation(ref op);");
    L.push("    if (rc == 0 && op.fAnyOperationsAborted) return -1;");
    L.push("    return rc;");
    L.push("  }");
    L.push("}");
    L.push("'@");
    L.push("}");
    L.push("$S = @{ Done = 0; Skipped = 0; Failed = 0; Planned = 0; Bytes = [int64]0 }");
    L.push("function Send-DcRecycle([string]$p) {");
    L.push("  if (Test-DcGuarded $p) { Write-Host \"    ! korumalı, atlandı: $p\"; return $false }");
    L.push("  $it = Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue");
    L.push("  if ($null -eq $it) { return $false }");
    L.push("  if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) { Write-Host \"    - junction/symlink, atlandı: $p\"; return $false }");
    L.push("  if (-not $Apply) { return $true }");
    L.push("  $rc = [DcRecycle]::Send($it.FullName)");
    L.push("  if ($rc -ne 0 -or (Test-Path -LiteralPath $p)) { $S.Failed++; return $false }");
    L.push("  return $true");
    L.push("}");
    L.push("function Test-DcRecent([string]$dir, [datetime]$cutoff) {");
    L.push("  $stack = New-Object System.Collections.Stack; $stack.Push($dir); $seen = 0");
    L.push("  while ($stack.Count -gt 0) {");
    L.push("    $d = $stack.Pop()");
    L.push("    try { $entries = Get-ChildItem -LiteralPath $d -Force -ErrorAction Stop } catch { return $true }");
    L.push("    foreach ($e in $entries) {");
    L.push("      if ($e.LastWriteTime -gt $cutoff) { return $true }");
    L.push("      if ($e.PSIsContainer -and -not ($e.Attributes -band [IO.FileAttributes]::ReparsePoint)) { $stack.Push($e.FullName) }");
    L.push("      $seen++; if ($seen -gt 200000) { return $true }");
    L.push("    }");
    L.push("  }");
    L.push("  return $false");
    L.push("}");
    L.push("function Start-DcItem([string]$cat, [string]$title, [string]$human, [int64]$bytes, [string]$p, [string[]]$procs, [bool]$admin) {");
    L.push("  Write-Host ''; Write-Host \"[$cat] $title  ($human)\"; Write-Host \"    $p\"");
    L.push("  if ($admin -and -not $IsAdmin) { Write-Host '    - yönetici gerekir; script yönetici olarak çalışmıyor. Atlandı'; $S.Skipped++; return $false }");
    L.push("  $it = Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue");
    L.push("  if ($null -eq $it) { Write-Host '    - artık yok / erişilemiyor, atlandı'; $S.Skipped++; return $false }");
    L.push("  if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) { Write-Host '    - junction/symlink, atlandı'; $S.Skipped++; return $false }");
    L.push("  if (Test-DcGuarded $p) { Write-Host '    ! KORUMALI (NEVER) yol, atlandı'; $S.Skipped++; return $false }");
    L.push("  if ($procs -and $procs.Count -gt 0) {");
    L.push("    $r = Get-Process -Name $procs -ErrorAction SilentlyContinue | Select-Object -First 1");
    L.push("    if ($r) { Write-Host (\"    - '\" + $r.ProcessName + \"' çalışıyor; kapatıp tekrar dene. Atlandı\"); $S.Skipped++; return $false }");
    L.push("  }");
    L.push("  $S.Planned++; $S.Bytes += $bytes; return $true");
    L.push("}");
    L.push("function Invoke-DcTrash([string]$p) {");
    L.push("  if (-not $Apply) { Write-Host '    -> Geri Dönüşüm Kutusuna taşınacak'; return }");
    L.push("  if (Send-DcRecycle $p) { Write-Host '    -> Geri Dönüşüm Kutusuna taşındı'; $S.Done++ } else { Write-Host '    ! taşınamadı (kilitli olabilir)' }");
    L.push("}");
    L.push("function Invoke-DcChildren([string]$p, [int]$days) {");
    L.push("  $cutoff = (Get-Date).AddDays(-$days); $n = 0; $k = 0");
    L.push("  foreach ($c in @(Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue)) {");
    L.push("    $n++");
    L.push("    if ($days -gt 0) {");
    L.push("      if ($c.LastWriteTime -gt $cutoff) { continue }");
    L.push("      if ($c.PSIsContainer -and (Test-DcRecent $c.FullName $cutoff)) { continue }");
    L.push("    }");
    L.push("    if (Send-DcRecycle $c.FullName) { $k++ }");
    L.push("  }");
    L.push("  if ($Apply) { Write-Host \"    -> $k / $n öğe Geri Dönüşüm Kutusuna taşındı (kilitli/yeni olanlar atlandı)\"; $S.Done++ }");
    L.push("  else { $w = ''; if ($days -gt 0) { $w = \" (son $days günde değişmeyenler)\" }; Write-Host \"    -> içindeki $k / $n öğe taşınacak$w\" }");
    L.push("}");
    L.push("function Invoke-DcCommand([string]$tool, [string]$cmd, [string]$fb, [string]$p) {");
    L.push("  $ok = $false");
    L.push("  if ($tool -match '[\\\\/]') { $ok = Test-Path -LiteralPath $tool } else { $ok = [bool](Get-Command $tool -ErrorAction SilentlyContinue) }");
    L.push("  if ($ok) {");
    L.push("    Write-Host \"    -> resmi komut: $cmd\"");
    L.push("    if ($Apply) {");
    L.push("      try { $global:LASTEXITCODE = 0; Invoke-Expression $cmd; if ($LASTEXITCODE -ne 0) { throw \"çıkış kodu $LASTEXITCODE\" }; $S.Done++ }");
    L.push("      catch { Write-Host \"    ! komut hata verdi: $_\"; $S.Failed++ }");
    L.push("    }");
    L.push("  } elseif ($fb -eq 'trash') { Write-Host \"    - '$tool' bulunamadı; yedek: klasör Geri Dönüşüm Kutusuna\"; Invoke-DcTrash $p }");
    L.push("  elseif ($fb -eq 'trash_children') { Write-Host \"    - '$tool' bulunamadı; yedek: içerik Geri Dönüşüm Kutusuna\"; Invoke-DcChildren $p 0 }");
    L.push("  else { Write-Host \"    - '$tool' bulunamadı; atlandı\"; $S.Skipped++ }");
    L.push("}");
    L.push("");
    L.push("$mode = 'DRY-RUN'; if ($Apply) { $mode = 'UYGULA' }");
    L.push("Write-Host \"=== disk-cleaner $mode ===\"");
    L.push("if (-not $Apply) { Write-Host 'DRY-RUN: hiçbir dosya değişmeyecek.' }");
    L.push("if ($Apply -and -not $Yes) {");
    L.push("  $ans = Read-Host " + qps("Seçili " + items.length + " öğe Geri Dönüşüm Kutusuna taşınacak / resmi komutları çalıştırılacak. Devam için EVET yaz"));
    L.push("  if ($ans -cne 'EVET') { Write-Host 'Vazgeçildi.'; exit 1 }");
    L.push("}");
    items.forEach(function (it) {
      L.push("");
      L.push("# ---- #" + it.id + " " + oneLine(it.title));
      var human = fmtBytes(it.size) + (it.reclaim === "partial" ? ", kısmi" : it.reclaim === "unknown" ? ", ölçülemedi" : "");
      var procs = (it.procs || []).length ? "@(" + it.procs.map(qps).join(", ") + ")" : "@()";
      var body;
      if (it.kind === "trash") body = "Invoke-DcTrash " + qps(it.path);
      else if (it.kind === "trash_children") body = "Invoke-DcChildren " + qps(it.path) + " 0";
      else if (it.kind === "trash_old") body = "Invoke-DcChildren " + qps(it.path) + " " + (it.older || 7);
      else if (it.kind === "command") body = "Invoke-DcCommand " + qps(it.tool || "") + " " + qps(it.cmd || "") + " " + qps(it.fallback || "") + " " + qps(it.path);
      else return;
      if (it.path === "docker://") {
        L.push("Write-Host ''; Write-Host " + qps("[" + CAT[it.cat] + "] " + oneLine(it.title) + "  (" + human + ")") + "; $S.Planned++");
        L.push("Invoke-DcCommand " + qps(it.tool || "docker") + " " + qps(it.cmd || "") + " '' ''");
        return;
      }
      L.push("if (Start-DcItem " + [qps(CAT[it.cat]), qps(oneLine(it.title)), qps(human), String(it.reclaim === "full" ? (it.size || 0) : 0), qps(it.path), procs, it.admin ? "$true" : "$false"].join(" ") + ") { " + body + " }");
    });
    L.push("");
    L.push("Write-Host ''");
    L.push("Write-Host '======================================================='");
    L.push("if ($Apply) {");
    L.push("  Write-Host (\"Tamamlanan: \" + $S.Done + \"  Atlanan: \" + $S.Skipped + \"  Hata: \" + $S.Failed)");
    L.push("  Write-Host 'Yer, Geri Dönüşüm Kutusu boşaltıldığında açılır. Önce içeriğine göz at.'");
    L.push("} else {");
    L.push("  Write-Host (\"DRY-RUN: \" + $S.Planned + \" öğe işlenecekti (tam ölçülen ~\" + [math]::Round($S.Bytes / 1MB) + \" MB). Atlanacak: \" + $S.Skipped)");
    L.push("  Write-Host 'Uygulamak için aynı komutu -Apply ile çalıştır.'");
    L.push("}");
    return L.join("\r\n") + "\r\n";
  }

  function generate(data, selectedItems) {
    var items = sortItems(selectedItems.filter(actionable));
    var win = data.env.os === "windows";
    return {
      shell: win ? "powershell" : "bash",
      filename: "disk-cleanup-" + stamp() + (win ? ".ps1" : ".sh"),
      content: win ? genPs(data, items) : genBash(data, items),
      count: items.length,
      totals: totals(items)
    };
  }

  var api = { generate: generate, qsh: qsh, qps: qps, fmtBytes: fmtBytes, actionable: actionable, totals: totals };
  root.DiskCleanerScriptGen = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
