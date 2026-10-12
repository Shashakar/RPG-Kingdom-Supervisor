# C5h Windows service identity acceptance (Supervisor #228)

**Status: code plus Windows compilation checks only. Not installed or verified on the actual host.** This phase adds a Windows ServiceBase executable under the dedicated Windows virtual service account, with a restricted, service-specific SID and private NTFS storage. The service only writes a fixed, compiled-in synthetic text fixture to private storage and validates its own service SID and directory ACL. No listener, IPC endpoint, stage path, snapshot request, secret, WebAuthn, promotion, or live game asset write is provided.

### Trust boundary

Unity executes worker-authored C# as the normal Windows user. The protective boundary assumes that user executes Unity **unelevated** and cannot escalate or alter Windows service/NTFS ACLs. A privileged administrator, SYSTEM compromise, or tampered installer is out of scope. Service isolation is NOT independent human authorization and cannot yet approve promotion.

### Stage reviewed installer source under administrator-only NTFS ownership

**Do not run elevated PowerShell directly from a worker-writable Git checkout or WSL mount.** A worker could edit an installer before an administrator executes it. The operator must review the exact GitHub PR revision and independently acquire matching copies of:

- scripts/windows/C5hSnapshotGuardian.cs
- scripts/windows/Install-C5hSnapshotGuardian.ps1
- scripts/windows/Test-C5hSnapshotGuardian.ps1
- scripts/windows/Test-C5hWorkerDenied.ps1

Copy the reviewed C# source and installer to the dedicated trusted-source directory. The parent and source directory must have disabled ACL inheritance, only SYSTEM/Administrators allowed, and BUILTIN\Administrators as OWNER. Files must also be SYSTEM/Administrators-owned. A suitable administrative **preparation example** is:

    $base = Join-Path $env:ProgramData 'RPGKingdomSupervisor\C5h'
    $trusted = Join-Path $base 'trusted-source'
    New-Item -ItemType Directory -Force -Path $trusted
    foreach ($p in @($base,$trusted)) {
      icacls.exe $p /setowner '*S-1-5-32-544'
      icacls.exe $p /inheritance:r
      icacls.exe $p /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
    }
    # Copy vetted exact source and installer into $trusted from trusted storage.
    icacls.exe (Join-Path $trusted 'C5hSnapshotGuardian.cs') /setowner '*S-1-5-32-544'
    icacls.exe (Join-Path $trusted 'Install-C5hSnapshotGuardian.ps1') /setowner '*S-1-5-32-544'
    Get-FileHash (Join-Path $trusted 'C5hSnapshotGuardian.cs') -Algorithm SHA256

Verify the expected SHA256 independently of any worker-supplied terminal output or issue prose; using an arbitrary hash supplied by the worker does not validate trust.

From the elevated, operator-controlled Windows PowerShell 5.1 shell, run the separately inspected installer from trusted-source using the independently reviewed C# hash:

    & (Join-Path $trusted 'Install-C5hSnapshotGuardian.ps1') -ApprovedSourceSha256 'FULL_INDEPENDENTLY_REVIEWED_CSHARP_SHA256'

The installer refuses user-owned/inherited/linked trusted source, mismatched approved source SHA256, preexisting service or installation destination. It compiles from a separate protected copy of the C# source and assigns the executable and data directory ownership to Administrators.

It registers SCM service RPGKSnapshotGuardian under NT SERVICE\RPGKSnapshotGuardian with restricted service SID NT SERVICE\RPGKSnapshotGuardian, hardens service control to SYSTEM/Administrators, and permits only that service SID (plus SYSTEM/Administrators) to access service-private data. The binary is read-only for the unique virtual service account. The service is configured for MANUAL start, with no worker-command or auto-start path.

### Verify on the actual Windows host

After review and installation, a privileged operator deliberately starts only the synthetic fixture service:

    sc.exe start RPGKSnapshotGuardian
    # Run Test-C5hSnapshotGuardian.ps1 from a separately reviewed trusted copy.

Test-C5hSnapshotGuardian.ps1 verifies the live SCM process PID and user SID, executable/parent ownership, private directory ACEs, and exact source/snapshot synthetic SHA256 and fixture receipt. It returns flags denying real-stage use, stage freeze, independent human authorization and copyback.

Separately run Test-C5hWorkerDenied.ps1 as the **actual unelevated Unity Windows user**. The negative test refuses elevated execution and verifies that this identity cannot enumerate private storage or start the protected SCM service. An absent service or ambiguous failure is not counted as a permission denial. Any unexpectedly allowed operation invalidates the result.

The operator may stop the service explicitly after acceptance:

    sc.exe stop RPGKSnapshotGuardian

Do not erase failure evidence, force an active Editor shutdown, grant a new worker permission, unhalt game GH-260 or copy assets as part of debugging.

### Verified by C5h code versus still missing

| Capability | Status |
| --- | --- |
| Windows ServiceBase compilation | Windows CI check; host execution pending |
| Separate dedicated virtual account process and service-specific SID | Provisioning implemented; requires host attestation |
| Service data ACL and Administrators ownership | Provisioning + audit implemented; requires host attestation |
| Worker denied reading private files and starting service | Negative test provided; requires actual worker-host run |
| Protected service-generated snapshot | Synthetic fixture only |
| Stage quiescence/Windows exclusion and real Unity snapshot | Not implemented |
| Independent human user verification and authorization | Not implemented |
| Live copyback, nonce consumption, automatic merge | Disabled |

### Next steps

Extend the service only AFTER host SID/ACL tests pass, using a safe operator-authorized stage request with a pinned grant, source revision, Windows process creation times and full process census, stage filesystem/volume handle attestation, non-following file handles, complete Unity GUID inventory and robust crash/recovery journal. Never expose an unguarded RPC, worker-controlled snapshot path, or copyback permission. A separate independent human challenge must be implemented before promotion.

Current work stays on stacked draft PRs under #228. The game issue #260 remains halted.
