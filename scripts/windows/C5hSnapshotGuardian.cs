// C5h #228: service-account fixture attestation only, NOT real asset snapshot.
// Compiled for Windows .NET Framework 4.8 using the inbox PowerShell Add-Type
// compiler. No IPC, CLI switch, user-controlled input, approval or copyback.
using System;
using System.Globalization;
using System.IO;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;

internal sealed class RPGKSnapshotGuardian : ServiceBase
{
    internal const string ExpectedServiceName = "RPGKSnapshotGuardian";
    private static readonly SecurityIdentifier LocalService =
        new SecurityIdentifier(WellKnownSidType.LocalServiceSid, null);
    private static readonly SecurityIdentifier BuiltinAdmins =
        new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
    private static readonly SecurityIdentifier LocalSystem =
        new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);

    public RPGKSnapshotGuardian()
    {
        ServiceName = ExpectedServiceName;
        CanStop = true;
        CanShutdown = true;
        AutoLog = false; // Do not implicitly create an Event Log source.
    }

    private static bool Same(SecurityIdentifier a, SecurityIdentifier b)
    {
        return a != null && b != null && a.Equals(b);
    }

    private static SecurityIdentifier ServiceSid()
    {
        return (SecurityIdentifier)(new NTAccount(
            "NT SERVICE\\" + ExpectedServiceName).Translate(typeof(SecurityIdentifier)));
    }

    private static void VerifyToken(SecurityIdentifier serviceSid)
    {
        using (WindowsIdentity identity = WindowsIdentity.GetCurrent(TokenAccessLevels.Query))
        {
            if (!Same(identity.User, LocalService))
                throw new InvalidOperationException("service process must run as LocalService");

            bool hasServiceSid = false;
            foreach (IdentityReference group in identity.Groups)
            {
                SecurityIdentifier sid = group as SecurityIdentifier;
                if (Same(sid, serviceSid))
                {
                    hasServiceSid = true;
                    break;
                }
            }
            if (!hasServiceSid)
                throw new InvalidOperationException("process token lacks service-specific SID");
        }
    }

    private static void VerifyPrivateAcl(string privatePath, SecurityIdentifier serviceSid)
    {
        DirectoryInfo directory = new DirectoryInfo(privatePath);
        if (!directory.Exists ||
            (directory.Attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("protected service root missing or linked");

        DirectorySecurity acl = directory.GetAccessControl(AccessControlSections.Access);
        if (!acl.AreAccessRulesProtected)
            throw new InvalidOperationException("protected root still inherits ACL entries");

        bool hasServiceWrite = false;
        AuthorizationRuleCollection rules = acl.GetAccessRules(
            true, true, typeof(SecurityIdentifier));
        foreach (AuthorizationRule rule in rules)
        {
            FileSystemAccessRule access = rule as FileSystemAccessRule;
            if (access == null)
                throw new InvalidOperationException("unrecognized ACL entry");
            SecurityIdentifier sid = access.IdentityReference as SecurityIdentifier;
            if (sid == null ||
                !(Same(sid, serviceSid) || Same(sid, LocalSystem) ||
                  Same(sid, BuiltinAdmins)))
                throw new InvalidOperationException("unexpected protected root ACL principal");
            if (Same(sid, serviceSid) && access.AccessControlType == AccessControlType.Allow
                && (access.FileSystemRights & FileSystemRights.WriteData) != 0)
                hasServiceWrite = true;
        }
        if (!hasServiceWrite)
            throw new InvalidOperationException("service SID does not have explicit write");
    }

    private static void WriteDurable(string path, byte[] bytes)
    {
        using (FileStream stream = new FileStream(
            path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(true);
        }
    }

    private static string Digest(byte[] data)
    {
        using (SHA256 sha = SHA256.Create())
        {
            return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant();
        }
    }

    private static string QuoteJson(string text)
    {
        return "\"" + text.Replace("\\", "\\\\").Replace("\"", "\\\"")
            .Replace("\r", "\\r").Replace("\n", "\\n") + "\"";
    }

    private static void MakeProofFixture(SecurityIdentifier serviceSid)
    {
        string root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "RPGKingdomSupervisor", "C5h", "private");
        VerifyPrivateAcl(root, serviceSid);

        // The only file bytes this service can copy are compiled-in synthetic
        // content. It must never read a real Unity stage until the separate
        // protected request protocol and Windows handle/volume checks exist.
        byte[] input = Encoding.UTF8.GetBytes(
            "RPG Kingdom C5h synthetic fixture; not a Unity stage.\n");
        string generation = "fixture-" + Guid.NewGuid().ToString("N");
        string folder = Path.Combine(root, generation);
        Directory.CreateDirectory(folder); // Inherits the protected root DACL.
        WriteDurable(Path.Combine(folder, "source-fixture.txt"), input);
        WriteDurable(Path.Combine(folder, "snapshot-fixture.txt"), input);
        string hash = Digest(input);
        string timestamp = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
        string receipt = "{"
            + "\"schemaVersion\":1,"
            + "\"mode\":\"protected-service-synthetic-fixture-only\","
            + "\"generation\":" + QuoteJson(generation) + ","
            + "\"windowsIdentitySid\":" + QuoteJson(LocalService.Value) + ","
            + "\"serviceSid\":" + QuoteJson(serviceSid.Value) + ","
            + "\"sourceFixtureSha256\":" + QuoteJson(hash) + ","
            + "\"snapshotFixtureSha256\":" + QuoteJson(hash) + ","
            + "\"createdUtc\":" + QuoteJson(timestamp) + ","
            + "\"realStageUsed\":false,"
            + "\"stageFrozen\":false,"
            + "\"independentHumanAuthorization\":false,"
            + "\"promotionAuthorized\":false,"
            + "\"copyback\":false"
            + "}\n";
        WriteDurable(Path.Combine(folder, "receipt.json"), Encoding.UTF8.GetBytes(receipt));
    }

    protected override void OnStart(string[] args)
    {
        if (args != null && args.Length != 0)
            throw new InvalidOperationException("service refuses start parameters");
        SecurityIdentifier serviceSid = ServiceSid();
        VerifyToken(serviceSid);
        MakeProofFixture(serviceSid);
        // No listener, port, pipe, worker-spawn, polling loop or promotion API.
    }

    protected override void OnStop()
    {
        // No stage or service-owned job state to flush/rollback in this slice.
    }

    public static void Main(string[] args)
    {
        if (args == null || args.Length != 0)
            throw new InvalidOperationException("service takes no CLI arguments");
        ServiceBase.Run(new RPGKSnapshotGuardian());
    }
}
