// Local structural types for the parts of the Sign in with ChatGPT DevKit (`@siwc/local`)
// that this sidecar uses. They are written here (not imported) so the sidecar compiles and
// is testable without the DevKit installed. They mirror the DevKit's public API.

export interface SessionIdentity {
  /** A stable account id, if the DevKit offers one. The bridge treats it as optional and
   * falls back to the email (see docs/agent-loop/siwc-probe.md). */
  id?: string;
  name?: string;
  email?: string;
}

export interface SessionError {
  code: string;
  message: string;
  retryable?: boolean;
  status?: number;
}

export interface SessionState {
  status: "disconnected" | "connecting" | "connected" | "reauth_required";
  /** True only when connected, credentials exist, and plan usage (token sharing) was granted. */
  sharing: boolean;
  profileId?: string;
  identity?: SessionIdentity;
  error?: SessionError;
}

export interface ChatGPTModel {
  slug: string;
  displayName: string;
}

export interface SignInOptions {
  signal?: AbortSignal;
  reconsent?: boolean;
  /** Register another ChatGPT account in this directory instead of reauthorizing the saved one. */
  newProfile?: boolean;
}

export interface StreamResponseOptions {
  model: string;
  input: string;
  instructions?: string;
  signal?: AbortSignal;
}

export interface ChatGPTClient {
  signIn(options?: SignInOptions): Promise<SessionState>;
  cancelSignIn(): void;
  getSession(): Promise<SessionState>;
  disconnect(): Promise<void>;
  listModels(options?: { signal?: AbortSignal }): Promise<ChatGPTModel[]>;
  streamResponse(options: StreamResponseOptions): Promise<{ text: string }>;
}

export interface CredentialEncryption {
  id: string;
  isAvailable(): boolean | Promise<boolean>;
  encrypt(plaintext: string): Uint8Array | Promise<Uint8Array>;
  decrypt(ciphertext: Uint8Array): string | Promise<string>;
}

export interface ChatGPTConfig {
  appName: string;
  appId: string;
  redirectPort: number;
  storageDir?: string;
  credentialEncryption: CredentialEncryption;
  openBrowser?: (url: string) => Promise<void> | void;
}

export interface DevkitModule {
  createChatGPT(config: ChatGPTConfig): ChatGPTClient;
}
