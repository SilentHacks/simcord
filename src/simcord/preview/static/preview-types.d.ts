export type Primitive = string | number | boolean | null;
export type Json = Primitive | Json[] | { [key: string]: Json | undefined };
export interface Identity {
  id?: string; value?: string | number | boolean; name?: string; label?: string; username?: string;
  kind?: string; avatar?: string; avatarAssetId?: string; bot?: boolean; color?: number;
  icon_color?: number; status?: string; discriminator?: string; display_name?: string;
  avatar_kind?: string; avatar_available?: boolean; presence?: string; role_color?: number;
  webhook?: boolean; members?: number; default?: boolean; description?: string; emoji?: Emoji;
}
export interface Choice extends Identity { value: string | number | boolean; name: string; description?: string }
export interface CommandOption {
  name: string; type: string | number; description?: string; required?: boolean; autocomplete?: boolean;
  choices?: Choice[]; minLength?: number; maxLength?: number; minValue?: number; maxValue?: number;
  fileTypes?: string[]; file_types?: string[]; channelTypes?: number[];
}
export interface CommandEntry {
  key: string; commandId: string; path: readonly string[]; invocation: string; description: string;
  schemaFingerprint: string; options: CommandOption[];
}
export interface CommandCatalog {
  state: string; entries: CommandEntry[]; truncated?: boolean;
  application?: { name: string; avatarAssetId?: string };
}
export interface CommandValue { raw: string | boolean; file?: File; entity?: Identity; label?: string }
export interface CommandDraft {
  id: number; version: number; commandId: string; path: readonly string[]; invocation: string;
  schemaFingerprint: string; description: string; available: boolean; focused: string | null;
  values: Readonly<Record<string, Readonly<CommandValue>>>; errors: Readonly<Record<string, string>>;
  added: readonly string[]; droppedOptions: readonly string[]; openOptionsOnRender: boolean;
}
export interface MutableCommandDraft extends Omit<CommandDraft, 'values' | 'errors' | 'added' | 'droppedOptions'> {
  values: Record<string, CommandValue>; errors: Record<string, string>; added: Set<string>; droppedOptions: string[];
}
export interface Diagnostic {
  id?: string; code: string; category?: string; severity?: string; state?: string; complete?: boolean;
  message?: string; detail?: string; remediation?: string; renderOwner?: string; correlation?: string;
  subject?: { messageId?: string; controlKey?: string; commandOption?: string };
}
export interface Asset {
  available?: boolean; diagnostic?: string; contentType?: string; mediaKind?: string; duration?: number;
  sourceCodecs?: Json; displayCodecs?: Json; transformation?: Json; qualityDifferences?: Json;
  effectiveMediaTime?: number; workerMemoryLimited?: boolean; displayReady?: boolean;
  displayWidth?: number; displayHeight?: number; waveform?: number[];
}
export interface AssetOptions { download?: boolean; capture?: boolean; poster?: boolean; mediaTime?: number | null }
export interface Profile {
  theme?: string; width: number; height: number; locale: string; timezone: string; deviceScale?: number;
  reducedMotion: boolean; mediaTime: number | null; presentationTime?: string | null;
}
export interface Dimensions { width: number; height: number }
export interface Presentation {
  display: string; layout: string; viewport: Dimensions; host: Dimensions;
  exactProfile: Dimensions; constraint?: string | null;
}
export interface CandidateDescriptor {
  query?: string; selected?: Identity[]; entries?: Identity[]; candidates?: Identity[];
  hasNext?: boolean; hasPrevious?: boolean; previousCursor?: string | null; nextCursor?: string | null; truncated?: boolean; state?: string;
}
export type DraftValue = string | boolean | ReadonlyArray<string | File> | null;
export interface Dropdown {
  key: string; selected: string[]; multi: boolean; minimum: number; maximum: number;
  modal: boolean; optional: boolean; highlight: string | null;
}
export interface Outcome { kind: string; messageId?: string }
export interface Receipt {
  requestId: string; sequence: number; expectedSequence?: number; revision: number;
  rejected: boolean; settlement: string; dispatched?: boolean; dispatch?: string;
  acknowledgement?: string; presentation?: string; uncertain?: boolean; correlation?: string;
  target?: { messageId?: string; controlKey?: string } | null; outcomes?: Outcome[];
  diagnostics?: Diagnostic[]; command?: { invocation?: string } | null;
  result?: { answered?: boolean; choices?: Choice[] };
}
export interface ActionBody {
  kind?: string; protocol_version?: number; sequence?: number; request_id?: string; generation?: number;
  bot_generation?: number; published_revision?: number; control_key?: string; target_id?: string | null;
  command_id?: string; path?: readonly string[]; focused?: string; schema_fingerprint?: string;
  options?: Readonly<Record<string, Primitive | { upload: number }>>;
  values?: readonly string[] | Readonly<Record<string, DraftValue>>;
  selected_values?: readonly string[]; query?: string; value?: string; cursor?: string | null; modal_handle?: string;
  host_width?: number; host_height?: number; width?: number; height?: number; layout?: string; display?: string;
  viewer_id?: string; content?: string; reply_to_id?: string | null; direction?: string; filter?: string;
  confirmed?: boolean; pinned?: boolean; emoji?: string; reacted?: boolean; answer_ids?: string[];
}
export interface ActionMetadata {
  draftId?: number | null; draftVersion?: number | null; commandDraftId?: number | null;
  commandDraftVersion?: number | null; uploads?: Readonly<Record<string, File>>;
}
export interface QueryIntent {
  key: string; kind: string; body: ActionBody; metadata: ActionMetadata | null; scope: string; token: number;
}
export interface PendingQuery {
  kind: string; key: string; controlKey: string | null; requestId: string; sequence: number; query: string;
}
export interface AdmittedAction {
  kind: string; contextId: string | null; requestId: string; sequence: number; controlKey: string | null;
  targetId: string | null; commandEnvelope: ActionBody | null; commandDraftId: number | null;
  commandDraftVersion: number | null; uploads: Readonly<Record<string, File>>; queryBody: ActionBody | null;
  queryMetadata: ActionMetadata | null; queryIntent: QueryIntent | null; botGeneration: number;
  draftVersion: number; closeProbe: boolean; queryPending: boolean; queryStatus: PendingQuery | null;
  contextGeneration: number; publishedRevision: number; submittedValues: ActionBody['values'] | null;
}
export interface ActionContext {
  contextId: string | null; contextGeneration: number; botGeneration: number; publishedRevision: number;
  observedRevision: number; viewerId: string | null; targetId: string | null; closed: boolean;
  authorized: boolean; protocolCompatible: boolean; pinnedCapture: boolean;
}
export interface ActionReport {
  pendingAction: AdmittedAction | null; uncertainAction: AdmittedAction | null; pendingQuery: PendingQuery | null;
  queuedQuery: QueryIntent | null; queuedQueryCount: number; queuedPageIntents: Readonly<Record<string, ActionBody>>;
  awaitingRevision: number | null; queryResultRevision: number | null;
}
export interface FontStatus {
  loaded: string[]; missing: {family: string; sample: string; error: string}[];
  faces: {family: string; sample: string; loaded: boolean}[];
}
export interface TransportReport {
  state: string; failures: number; recoveries: number; uncertainRequestId: string | null; uncertainSequence: number | null;
  history: readonly {state: string; code: string; at: number}[]; lastSuccessfulRead?: string | null;
}
export interface MessageIndexEntry {
  id: string; author?: Identity; createdAt?: string; excerpt?: string; contentKinds?: string[];
  attachments?: {count?: number}; components?: unknown[]; ephemeral?: boolean;
}
export interface Message {
  id: string; timestamp: string; content?: string; pinned?: boolean; poll?: Poll;
  author?: Identity; edited_timestamp?: string; flags?: number; compact?: boolean; ephemeral?: boolean;
  content_tokens?: MarkdownToken[]; components_v2?: boolean; components?: Component[]; embeds?: Embed[];
  attachments?: Media[]; stickers?: Media[]; snapshots?: Message[]; allowed_actions?: string[];
  reactions?: Reaction[]; type_info?: {kind?: string}; reply?: Reply; thread?: Thread;
  system?: SystemMessage; interaction_header?: InteractionHeader;
}
export interface Snapshot {
  protocolVersion: number; context: {id: string; generation: number}; botGeneration: number;
  publishedRevision: number; viewerId: string | null; targetId: string | null; status?: string;
  runtimeVersion?: string; layout: string; profile?: Partial<Profile>;
  viewers?: (Identity | string)[]; channel?: { name?: string; recipient?: Identity; topic?: string;
    canSendMessages?: boolean; canUseApplicationCommands?: boolean };
  presentation: Presentation; publication: {publishedAt?: string}; messages: Record<string, Message>;
  messageIndex: MessageIndexEntry[]; timeline: string[]; history: {hasBefore?: boolean; hasAfter?: boolean};
  navigation: {query?: string; hasPrevious?: boolean; hasNext?: boolean; previousCursor?: string | null;
    nextCursor?: string | null; filter?: string}; candidates: Record<string, CandidateDescriptor>;
  activity: Receipt[]; lastAction?: Receipt | null; diagnostics?: Diagnostic[]; assets: Record<string, Asset>;
  commands?: {state?: string; fingerprint?: string}; modal?: {handle: string; payload: ModalComponent};
}
export interface ModalComponent extends Component {}
export interface Emoji {
  id?: string; name?: string; custom?: boolean; asset_id?: string; available?: boolean; animated?: boolean;
}
export interface MarkdownToken {
  type: string; content?: string; children?: MarkdownToken[]; language?: string; tag?: string;
  unix?: string | number; style?: string; emoji?: Emoji; label?: string; href?: string; group?: string | number;
}
export interface Media {
  id?: string; name?: string; asset_id?: string | null; available?: boolean; content_type?: string;
  filename?: string; size?: number; description?: string; spoiler?: boolean; inline?: boolean; preview?: string;
}
export interface PremiumPresentation {
  sku_id: string; name: string; price_text: string; locale: string; icon_asset_id?: string; icon_available?: boolean;
}
export interface Component {
  type?: number; id?: number; control_key?: string; custom_id?: string; components?: Component[];
  component?: Component; accessory?: Component; required?: boolean; disabled?: boolean; style?: number;
  value?: Primitive; default?: Primitive; label?: string; placeholder?: string; description?: string;
  min_length?: number; max_length?: number; min_values?: number; max_values?: number;
  options?: Identity[]; emoji?: Emoji; url?: string; sku_id?: string; sku_presentation?: PremiumPresentation;
  content?: string; markdown_tokens?: MarkdownToken[]; accent_color?: number | null; spoiler?: boolean;
  media?: Media; items?: {media?: Media; spoiler?: boolean; description?: string}[];
  divider?: boolean; spacing?: number; file?: Media; name?: string; size?: number;
  application_identity?: Identity; application_name?: string; title?: string;
}
export interface EmbedField { name?: string; value?: string; inline?: boolean; name_tokens?: MarkdownToken[]; value_tokens?: MarkdownToken[] }
export interface Embed {
  type?: string; color?: number; color_value?: number; provider?: {name?: string; url?: string};
  author?: {name?: string; url?: string; icon_asset_id?: string; icon_available?: boolean};
  title?: string; url?: string; title_tokens?: MarkdownToken[]; description?: string; description_tokens?: MarkdownToken[];
  thumbnail?: Media; image?: Media; video?: Media; fields?: EmbedField[]; timestamp?: string;
  footer?: {text?: string; icon_asset_id?: string; icon_available?: boolean};
}
export interface Poll {
  question: string; finalized?: boolean; expired?: boolean; multiselect?: boolean; total_votes: number; expiry: string;
  answers: {id: string; text?: string; emoji?: Emoji; viewer_selected?: boolean; count: number; percentage?: number}[];
}
export interface Reaction { can_toggle?: boolean; viewer_reacted?: boolean; emoji?: Emoji; count: number }
export interface Reply { state?: string; author?: Identity; channel_name?: string; excerpt_tokens?: MarkdownToken[] }
export interface Thread { name?: string; message_count: number; archived?: boolean }
export interface DiscordLink { url?: string; name?: string; author?: Identity }
export interface SystemMessage {
  icon?: string; text?: string; text_tokens?: MarkdownToken[]; recipient?: Identity;
  channel?: DiscordLink; reference?: DiscordLink;
}
export interface InteractionHeader {
  kind?: string; user?: Identity; name?: string; command_type?: string; target_user?: Identity; target_message?: DiscordLink;
}
export interface LightboxEntry {
  image: HTMLImageElement; label: string; media: Media & {asset_id: string}; author?: Identity;
  timestamp?: string; options: RenderOptions;
}
export interface LightboxGroup { items: LightboxEntry[] }
export interface MediaPlayer {
  wrapper: HTMLElement; element: HTMLAudioElement | HTMLVideoElement; waveform: HTMLElement; controls: HTMLElement;
  play: HTMLButtonElement; seek: HTMLInputElement; time: HTMLElement; mute: HTMLButtonElement;
  volume: HTMLInputElement; status: HTMLElement; options: RenderOptions; label: string; className: string;
  assetId: string; kind: 'audio' | 'video'; mediaTime: number | null; cancelWaiters: Set<() => void>;
  active: boolean; pending: Promise<unknown>[]; dispose: () => void; updateTime: () => void;
}
export interface ListboxOptions {
  list: HTMLElement; owner: HTMLElement; idPrefix: string;
  onActivate?: (item: HTMLElement, index: number, event: KeyboardEvent) => void;
  onCommit?: (item: HTMLElement | null, index: number) => void;
  onNavigate?: (item: HTMLElement, index: number) => void; onEscape?: () => void;
  isSelected?: (item: HTMLElement, index: number) => boolean; activeClass?: string; expanded?: boolean;
  wrap?: boolean; commitOnTab?: boolean; closeOnEscape?: boolean; activationKeys?: string[];
}
export interface RenderOptions {
  assets?: Readonly<Record<string, Readonly<Asset>>>; assetMetadata?: (id: string) => Readonly<Asset> | undefined;
  loadAsset?: (id: string, options?: AssetOptions) => Promise<string>;
  isCurrent?: () => boolean; onDiagnostic?: (diagnostic: Diagnostic) => void; pendingMedia?: Promise<unknown>[];
  locale?: string; timezone?: string; presentationTime?: string | null; mediaTime?: number | null; reducedMotion?: boolean;
  contextId?: string | null; viewerId?: string | null; scope?: string; messageId?: string;
  messageAuthor?: Identity; messageTimestamp?: string; lightboxGroup?: LightboxGroup;
  mediaPlayerReuse?: Map<string | undefined, HTMLElement[]>; onMediaCaptureTime?: (id: string, time: number) => void;
  isSpoilerRevealed?: (key: string) => boolean; onRevealSpoiler?: (key: string) => void;
  candidates?: Readonly<Record<string, CandidateDescriptor>>; dropdown?: Dropdown | null; modalHandle?: string;
  labelledBy?: string; validationError?: {controlId: string; message: string} | null; scrollTop?: number;
  draftValue?: (key: string) => DraftValue | undefined;
  initDraft?: (key: string, value: DraftValue) => DraftValue;
  isTouched?: (key: string) => boolean; onInit?: (key: string, value: DraftValue) => void;
  onReconcileDraft?: (key: string, values: string[]) => void;
  identityPending?: (key: string) => boolean; identityEntries?: (key: string) => Identity[];
  selectionInvalid?: (key: string) => boolean; selectionStatus?: (key: string) => string | undefined;
  candidateQuery?: (key: string) => string | undefined; candidateLoading?: (key: string) => boolean;
  onCandidateQuery?: (key: string, query: string, cursor: string | null, modal: string | null) => void;
  onOpen?: SelectOpen; onDraft?: SelectDraft; onCommit?: SelectCommit; onCancel?: (key?: string) => void;
  onNavigate?: (key: string, value: string) => void; onClear?: (key: string, minimum: number) => void;
  onClick?: (key: string) => void; onPremiumActivate?: (sku: string) => void; onLocalRender?: () => void;
  onSelectOpen?: SelectOpen; onSelectDraft?: SelectDraft; onSelectCommit?: SelectCommit; onSelectCancel?: (key?: string) => void;
  onFiles?: (key: string, files: File[]) => void; onSubmit?: (values: Record<string, DraftValue>) => void;
  channelLayout?: boolean; onReply?: MessageAction | null; onEdit?: MessageAction | null;
  onDelete?: MessageAction | null; onPin?: MessageAction | null;
  onReaction?: (message: Message, reaction: Reaction) => void;
  pollAnswers?: (message: Message) => string[] | undefined; pollShowingResults?: (message: Message) => boolean;
  onPollDraft?: (message: Message, answers: string[]) => void;
  onPollSubmit?: (message: Message, answers: string[]) => void;
  onPollResults?: (message: Message, showing: boolean) => void;
}
export type MessageAction = (message: Message) => void;
export type SelectOpen = (key: string, selected: string[], multi: boolean, minimum: number, maximum: number, highlight?: string, entries?: Identity[], modal?: boolean) => void;
export type SelectDraft = (key: string, value: DraftValue, multi?: boolean, minimum?: number, maximum?: number, selected?: string[], entry?: Identity) => void;
export type SelectCommit = (key: string, values?: string[]) => void;
export interface ModalControl {
  field: HTMLElement; control?: HTMLElement | null; focus?: HTMLElement | null; get: () => DraftValue; include: () => boolean;
}
export interface LottieAnimation {
  destroy(): void; setSubframe(value: boolean): void; addEventListener(name: string, callback: () => void): void;
  goToAndStop(frame: number, isFrame: boolean): void; play(): void;
}
declare global {
  interface HTMLElement { _previewDispose?: () => void; _previewMediaPlayer?: MediaPlayer }
  interface Window {
    bodymovin?: {loadAnimation(options: {container: HTMLElement; renderer: 'canvas'; loop: boolean; autoplay: boolean; animationData: unknown}): LottieAnimation};
  }
}
