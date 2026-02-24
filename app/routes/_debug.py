PS C:\Users\sirbm>
PS C:\Users\sirbm> Invoke-RestMethod "$Base/api/_boot" -Headers @{ "X-Admin-Key" = $AdminKey }

boot                                                                                                                      ok stric
                                                                                                                                 t
----                                                                                                                      -- -----
@{api_prefix=/api; cookie_mode=True; cors=; errors=System.Object[]; optional=System.Object[]; required=System.Object[]} True  True


PS C:\Users\sirbm> Invoke-RestMethod "$Base/api/_debug/subscription_health" -Headers @{ "X-Admin-Key" = $AdminKey }
Invoke-RestMethod : {"error":"Not Found","ok":false}
At line:1 char:1
+ Invoke-RestMethod "$Base/api/_debug/subscription_health" -Headers @{  ...
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : InvalidOperation: (System.Net.HttpWebRequest:HttpWebRequest) [Invoke-RestMethod], WebException
    + FullyQualifiedErrorId : WebCmdletWebResponseException,Microsoft.PowerShell.Commands.InvokeRestMethodCommand
PS C:\Users\sirbm> $secret = $env:PAYSTACK_WEBHOOK_SECRET  # or paste it directly
PS C:\Users\sirbm> $payloadObj = @{
>>   event = "charge.success"
>>   data = @{
>>     reference = "TEST_REF_123"
>>     amount = 100000
>>     currency = "NGN"
>>     metadata = @{
>>       account_id = "091ee3e5-e669-44f9-a555-24987c43fc1d"
>>       plan_code = "monthly"
>>       upgrade_mode = "now"
>>     }
>>   }
>> }
PS C:\Users\sirbm> $payload = ($payloadObj | ConvertTo-Json -Depth 20)
PS C:\Users\sirbm>
PS C:\Users\sirbm> # Compute HMAC-SHA512 of RAW body (utf8) using secret
PS C:\Users\sirbm> $hmac = New-Object System.Security.Cryptography.HMACSHA512
PS C:\Users\sirbm> $hmac.Key = [Text.Encoding]::UTF8.GetBytes($secret)
Exception calling "GetBytes" with "1" argument(s): "Array cannot be null.
Parameter name: chars"
At line:1 char:1
+ $hmac.Key = [Text.Encoding]::UTF8.GetBytes($secret)
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (:) [], MethodInvocationException
    + FullyQualifiedErrorId : ArgumentNullException

PS C:\Users\sirbm> $hashBytes = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($payload))
PS C:\Users\sirbm> $signature = ([BitConverter]::ToString($hashBytes) -replace "-", "").ToLower()
PS C:\Users\sirbm>
PS C:\Users\sirbm> Invoke-RestMethod "$Base/api/webhooks/paystack" `
>>   -Method Post `
>>   -Headers @{ "Content-Type"="application/json"; "x-paystack-signature"=$signature } `
>>   -Body $payload
Invoke-RestMethod : {"error":"invalid_signature","ok":false}
At line:1 char:1
+ Invoke-RestMethod "$Base/api/webhooks/paystack" `
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : InvalidOperation: (System.Net.HttpWebRequest:HttpWebRequest) [Invoke-RestMethod], WebException
    + FullyQualifiedErrorId : WebCmdletWebResponseException,Microsoft.PowerShell.Commands.InvokeRestMethodCommand
PS C:\Users\sirbm> # find folders containing an "app" directory
PS C:\Users\sirbm> Get-ChildItem C:\Users\sirbm -Directory -Recurse -ErrorAction SilentlyContinue |
>>   Where-Object { Test-Path (Join-Path $_.FullName "app") } |
>>   Select-Object -First 20 FullName

FullName
--------
C:\Users\sirbm\cre8-studio
C:\Users\sirbm\cre8hub-web
C:\Users\sirbm\naija-tax-guide
C:\Users\sirbm\naijatax-guide-frontend
C:\Users\sirbm\thecre8hub-web
C:\Users\sirbm\cre8-studio\node_modules\next\dist\build\segment-config
C:\Users\sirbm\cre8-studio\node_modules\next\dist\client\dev\hot-reloader
C:\Users\sirbm\cre8-studio\node_modules\next\dist\esm\build\segment-config
C:\Users\sirbm\cre8-studio\node_modules\next\dist\esm\client\dev\hot-re...
C:\Users\sirbm\cre8-studio\node_modules\next\dist\esm\next-devtools\use...
C:\Users\sirbm\cre8-studio\node_modules\next\dist\esm\server\normalizer...
C:\Users\sirbm\cre8-studio\node_modules\next\dist\next-devtools\userspace
C:\Users\sirbm\cre8-studio\node_modules\next\dist\server\normalizers\built
C:\Users\sirbm\cre8hub-web\.next\server
C:\Users\sirbm\cre8hub-web\.next\types
C:\Users\sirbm\cre8hub-web\.next\static\chunks
C:\Users\sirbm\cre8hub-web\.next\static\css
C:\Users\sirbm\cre8hub-web\node_modules\next\dist\client\components\rea...
C:\Users\sirbm\cre8hub-web\node_modules\next\dist\esm\client\components...
C:\Users\sirbm\cre8hub-web\node_modules\next\dist\esm\server\future\nor...


PS C:\Users\sirbm> python -c "from app.services.subscriptions_service import activate_subscription_now; print('OK', activate_subscription_now)"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    from app.services.subscriptions_service import activate_subscription_now; print('OK', activate_subscription_now)
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'app'
PS C:\Users\sirbm> Invoke-RestMethod "$Base/api/_boot"

boot
----
@{api_prefix=/api; cookie_mode=True; cors=; debug_routes_enabled=True; errors=System.Object[]; optional=System.Object[]; requir...


PS C:\Users\sirbm> Invoke-RestMethod "$Base/api/_debug/subscription_health" -Headers @{ "X-Admin-Key" = $AdminKey }
Invoke-RestMethod : {"error":"Not Found","ok":false}
At line:1 char:1
+ Invoke-RestMethod "$Base/api/_debug/subscription_health" -Headers @{  ...
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : InvalidOperation: (System.Net.HttpWebRequest:HttpWebRequest) [Invoke-RestMethod], WebException
    + FullyQualifiedErrorId : WebCmdletWebResponseException,Microsoft.PowerShell.Commands.InvokeRestMethodCommand
PS C:\Use
